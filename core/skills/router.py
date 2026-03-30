"""
스킬 라우터 — 트리거 매칭 + LLM 문맥 분석 + 신뢰도 측정

매칭 흐름:
1. 트리거 키워드 매칭 → 후보 스킬 + 신뢰도 계산
2. 트리거 미매칭 또는 신뢰도 부족 → LLM 문맥 분석 (description + body 기반)
3. 두 결과 병합 → 최고 후보 선택
4. 신뢰도 60 초과 → 바로 실행
5. 신뢰도 60 이하 → 사용자에게 재확인 (Discord 버튼 UI)
6. 매칭 없음 → None 반환 (일반 대화로 fallback)
"""

import json
import logging
import re
from pathlib import Path

from core.llm.base import BaseLLMClient
from core.skills.management_actions import get_management_keywords
from core.skills.models import Skill, SkillMatchResult, SkillMatchCandidate

logger = logging.getLogger("girey-bot.skills.router")

# 신뢰도 임계값 (0~100)
CONFIDENCE_THRESHOLD = 60

# 슬래시 명령어 정의에서 동적으로 생성된 관리 키워드
_MANAGEMENT_KEYWORDS = get_management_keywords()

# LLM 시스템 프롬프트 템플릿
_PROMPT_DIR = Path(__file__).parent / "prompts"
_ROUTER_PROMPT_TEMPLATE = (_PROMPT_DIR / "router_select.txt").read_text(encoding="utf-8")


class SkillRouter:
    """메시지 → 스킬 매칭을 수행하는 라우터"""

    def __init__(
        self,
        skills: dict[str, Skill],
        llm_client: BaseLLMClient,
    ):
        self.skills = skills
        self.llm_client = llm_client
        # 트리거별 역인덱스: trigger_keyword → [(skill, trigger)]
        self._trigger_index: dict[str, list[tuple[Skill, str]]] = {}
        self._build_trigger_index()

    def _build_trigger_index(self) -> None:
        """모든 스킬의 triggers를 역인덱스로 구축합니다."""
        self._trigger_index.clear()
        for skill in self.skills.values():
            if skill.disable_model_invocation:
                continue
            for trigger in skill.triggers:
                key = trigger.lower()
                if key not in self._trigger_index:
                    self._trigger_index[key] = []
                self._trigger_index[key].append((skill, trigger))

        logger.debug(
            f"트리거 인덱스 구축: {len(self._trigger_index)}개 키워드"
        )

    def update_skills(self, skills: dict[str, Skill]) -> None:
        """스킬 목록을 갱신하고 인덱스를 재구축합니다."""
        self.skills = skills
        self._build_trigger_index()

    async def route(self, message_content: str) -> SkillMatchResult:
        """
        메시지 내용을 분석하여 적합한 스킬을 찾습니다.

        1단계: 트리거 키워드 매칭
        2단계: 트리거 미매칭 또는 신뢰도 부족 시 LLM 문맥 분석
        3단계: 결과 병합 → 최고 후보 선택

        Returns:
            SkillMatchResult:
                - confidence > 60: skill이 설정되고 needs_clarification=False
                - confidence <= 60: candidates가 채워지고 needs_clarification=True
                - 매칭 없음: skill=None, candidates=[]
        """
        content_lower = message_content.lower()

        # ── 1단계: 트리거 키워드 매칭 ──
        candidates = self._match_triggers(content_lower)

        # ── 2단계: LLM 문맥 분석 ──
        # 트리거가 없거나 최고 신뢰도가 임계값 이하이면 LLM에게도 판단 위임
        best_trigger_conf = max(
            (c.confidence for c in candidates), default=0.0,
        )
        if best_trigger_conf <= CONFIDENCE_THRESHOLD:
            llm_candidates = await self._llm_select(message_content)
            candidates = self._merge_candidates(candidates, llm_candidates)

        if not candidates:
            return SkillMatchResult()

        # ── 3단계: 최고 신뢰도 후보 선택 ──
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        best = candidates[0]

        if best.confidence > CONFIDENCE_THRESHOLD:
            logger.info(
                f"스킬 매칭 확정: {best.skill.name} "
                f"(신뢰도={best.confidence:.0f}, "
                f"방식={best.reason})"
            )
            return SkillMatchResult(
                skill=best.skill,
                confidence=best.confidence,
                trigger_type="trigger" if best.matched_trigger else "llm",
                matched_trigger=best.matched_trigger,
                candidates=candidates,
                needs_clarification=False,
            )
        else:
            logger.info(
                f"스킬 매칭 불확실: 최고 신뢰도={best.confidence:.0f} "
                f"(임계값={CONFIDENCE_THRESHOLD}), "
                f"후보={[c.skill.name for c in candidates]}"
            )
            return SkillMatchResult(
                skill=None,
                confidence=best.confidence,
                candidates=candidates,
                needs_clarification=True,
            )

    @staticmethod
    def _merge_candidates(
        trigger_candidates: list[SkillMatchCandidate],
        llm_candidates: list[SkillMatchCandidate],
    ) -> list[SkillMatchCandidate]:
        """트리거 후보와 LLM 후보를 병합합니다.

        같은 스킬이 양쪽에 있으면 높은 신뢰도를 채택하고 reason을 합칩니다.
        """
        merged: dict[str, SkillMatchCandidate] = {}
        for c in trigger_candidates:
            merged[c.skill.name] = c

        for c in llm_candidates:
            existing = merged.get(c.skill.name)
            if existing is None:
                merged[c.skill.name] = c
            elif c.confidence > existing.confidence:
                # LLM 신뢰도가 더 높으면 교체하되 트리거 정보 보존
                c.reason = f"{existing.reason} + {c.reason}"
                c.matched_trigger = c.matched_trigger or existing.matched_trigger
                merged[c.skill.name] = c
            else:
                # 트리거 신뢰도가 더 높으면 LLM reason만 추가
                existing.reason = f"{existing.reason} + {c.reason}"

        return list(merged.values())

    def route_direct(self, skill_name: str) -> SkillMatchResult:
        """이름으로 직접 매칭 (슬래시 명령어용). 신뢰도 100."""
        skill = self.skills.get(skill_name)
        if skill is None:
            return SkillMatchResult()

        return SkillMatchResult(
            skill=skill,
            confidence=100.0,
            trigger_type="direct",
        )

    # ─── 트리거 키워드 매칭 ──────────────

    def _match_triggers(self, content_lower: str) -> list[SkillMatchCandidate]:
        """트리거 키워드로 후보 스킬을 매칭합니다."""
        # 스킬별로 매칭된 트리거 수를 집계
        skill_hits: dict[str, list[str]] = {}  # skill.name → [matched_triggers]

        for trigger_key, entries in self._trigger_index.items():
            if trigger_key in content_lower:
                for skill, original_trigger in entries:
                    if skill.name not in skill_hits:
                        skill_hits[skill.name] = []
                    skill_hits[skill.name].append(original_trigger)

        if not skill_hits:
            return []

        candidates = []
        for skill_name, matched_triggers in skill_hits.items():
            skill = self.skills[skill_name]
            confidence = self._calculate_trigger_confidence(
                skill, matched_triggers, content_lower
            )
            candidates.append(SkillMatchCandidate(
                skill=skill,
                confidence=confidence,
                matched_trigger=matched_triggers[0],
                reason=f"트리거 매칭: {', '.join(matched_triggers)}",
            ))

        return candidates

    def _calculate_trigger_confidence(
        self,
        skill: Skill,
        matched_triggers: list[str],
        content_lower: str,
    ) -> float:
        """
        트리거 매칭 기반 신뢰도를 계산합니다 (0~100).

        가중 요소:
        - 매칭된 트리거 비율 (해당 스킬 전체 트리거 대비)
        - 트리거 문자열 길이 (긴 트리거일수록 정확)
        - 매칭된 트리거 수 (여러 개 매칭되면 보너스)
        - 다른 스킬과 트리거 중복 여부 (중복되면 감점)
        """
        total_triggers = len(skill.triggers) or 1

        # 1. 매칭 비율 (0~40점)
        match_ratio = len(matched_triggers) / total_triggers
        ratio_score = match_ratio * 40

        # 2. 트리거 길이 점수 (0~30점) — 긴 트리거는 더 구체적
        max_trigger_len = max(len(t) for t in matched_triggers)
        length_score = min(max_trigger_len / 10, 1.0) * 30

        # 3. 복수 매칭 보너스 (0~15점)
        multi_bonus = min(len(matched_triggers) - 1, 3) * 5

        # 4. 중복 감점 — 같은 트리거가 다른 스킬에도 있으면 감점
        overlap_penalty = 0
        for trigger in matched_triggers:
            key = trigger.lower()
            if key in self._trigger_index:
                other_skills = len(self._trigger_index[key])
                if other_skills > 1:
                    overlap_penalty += 10 * (other_skills - 1)

        # 5. 스킬 관리 의도 감점 — "삭제해줘", "수정해줘" 등이 포함되면
        #    스킬을 실행하려는 것이 아니라 관리하려는 것이므로 큰 폭으로 감점
        management_penalty = 0
        for kw in _MANAGEMENT_KEYWORDS:
            if kw in content_lower:
                management_penalty = 50
                break

        confidence = (
            ratio_score + length_score + multi_bonus
            - overlap_penalty - management_penalty
        )
        return max(0.0, min(100.0, confidence))

    # ─── LLM 기반 스킬 선택 ──────────────

    async def _llm_select(
        self,
        message_content: str,
    ) -> list[SkillMatchCandidate]:
        """
        LLM에게 메시지 문맥을 분석하여 스킬 선택을 위임합니다.

        description, triggers, body(실행 절차 요약)를 모두 전달하여
        트리거 키워드가 없더라도 문맥적으로 유사한 요청을 매칭합니다.
        """
        if not self.llm_client or not self.llm_client.is_available:
            return []

        auto_skills = [
            s for s in self.skills.values()
            if not s.disable_model_invocation
        ]
        if not auto_skills:
            return []

        # 스킬 정보를 풍부하게 전달 (description + triggers + body 요약)
        skill_entries = []
        for s in auto_skills:
            triggers_str = ", ".join(s.triggers[:8]) if s.triggers else "(없음)"
            body_summary = s.body[:150] if s.body else ""
            skill_entries.append(
                f"### {s.name}\n"
                f"- 설명: {s.description}\n"
                f"- 트리거 키워드: {triggers_str}\n"
                f"- 실행 절차 요약:\n{body_summary}"
            )
        skill_list = "\n\n".join(skill_entries)

        system_prompt = _ROUTER_PROMPT_TEMPLATE.format(skill_list=skill_list)

        try:
            response = await self.llm_client.chat(
                prompt=message_content,
                system_prompt=system_prompt,
            )

            if not response.available or not response.content:
                return []

            # JSON 파싱 — ```json ... ``` 래핑 처리
            raw = response.content.strip()
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not json_match:
                return []

            result = json.loads(json_match.group())

            skill_name = result.get("skill")
            confidence = float(result.get("confidence", 0))
            reason = result.get("reason", "")

            if not skill_name or skill_name == "null":
                return []

            skill = self.skills.get(skill_name)
            if skill is None:
                return []

            return [SkillMatchCandidate(
                skill=skill,
                confidence=confidence,
                reason=f"문맥 분석: {reason}",
            )]

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"LLM 스킬 선택 응답 파싱 실패: {e}")
            return []
        except Exception as e:
            logger.error(f"LLM 스킬 선택 중 오류: {e}")
            return []
