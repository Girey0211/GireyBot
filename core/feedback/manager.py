"""
유저 피드백 점수 관리자

user_feedback 테이블의 CRUD 및 응답 모드 결정을 담당합니다.

점수 감소 규칙 (구간별 일(日) 감소율):
  >90  구간: 30/7 pts/day  → 초과분이 7일 후 90으로 복귀
  60~90 구간: 6   pts/day  → 30점 감소에 5일 소요
  0~60  구간: 12  pts/day  → 60점 감소에 5일 소요
"""

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger("girey-bot.feedback")

# 응답 모드 임계값 (누적 score 기준)
THRESHOLD_COLD    = 30   # 이상: 냉담한 응답
THRESHOLD_HOSTILE = 60   # 이상: 매우 부정적 응답
THRESHOLD_REFUSE  = 90   # 이상: 응답 거부

# 구간별 일(日) 감소율
_RATE_HIGH = 30 / 7   # >90  구간
_RATE_MID  = 30 / 5   # 60~90 구간
_RATE_LOW  = 60 / 5   # 0~60  구간


def _apply_decay(score: int, updated_at_str: str) -> int:
    """경과 시간에 따라 구간별 감소율을 적용하고 감소된 점수를 반환합니다."""
    if score <= 0:
        return 0

    try:
        updated_at = datetime.fromisoformat(updated_at_str)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        elapsed_days = (datetime.now(timezone.utc) - updated_at).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return score

    if elapsed_days <= 0:
        return score

    remaining = elapsed_days
    current   = float(score)

    # 구간 1: >90 → 90
    if current > 90:
        days_to_90 = (current - 90) / _RATE_HIGH
        if remaining >= days_to_90:
            current   = 90.0
            remaining -= days_to_90
        else:
            return max(0, int(current - remaining * _RATE_HIGH))

    # 구간 2: 60~90 → 60
    if current > 60 and remaining > 0:
        days_to_60 = (current - 60) / _RATE_MID
        if remaining >= days_to_60:
            current   = 60.0
            remaining -= days_to_60
        else:
            return max(0, int(current - remaining * _RATE_MID))

    # 구간 3: 0~60 → 0
    if current > 0 and remaining > 0:
        current -= remaining * _RATE_LOW

    return max(0, int(current))


def _response_mode_for(score: int) -> str:
    """점수에 따라 응답 모드를 반환합니다."""
    if score >= THRESHOLD_REFUSE:
        return "refuse"
    if score >= THRESHOLD_HOSTILE:
        return "hostile"
    if score >= THRESHOLD_COLD:
        return "cold"
    return "normal"


class FeedbackManager:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def get_score(self, user_id: int) -> int:
        """감소가 반영된 현재 유효 점수를 반환합니다. 변화가 있으면 DB도 갱신합니다."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT score, updated_at FROM user_feedback WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                return 0

            raw_score, updated_at_str = row
            decayed = _apply_decay(raw_score, updated_at_str)

            if decayed != raw_score:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                await db.execute(
                    "UPDATE user_feedback SET score = ?, updated_at = ? WHERE user_id = ?",
                    (decayed, now, user_id),
                )
                await db.commit()
                logger.debug(f"점수 감소 적용 — user={user_id}: {raw_score} → {decayed}")

            return decayed

    async def get_response_mode(self, user_id: int) -> str:
        """유저의 현재 응답 모드를 반환합니다: 'normal' | 'cold' | 'hostile' | 'refuse'"""
        return _response_mode_for(await self.get_score(user_id))

    async def add_violation(
        self,
        user_id: int,
        guild_id: int,
        violation_type: str,
        score_delta: int,
    ) -> int:
        """감소 적용 후 위반 점수를 추가하고 갱신된 누적 점수를 반환합니다."""
        # 감소를 먼저 반영한 현재 점수를 기준으로 더함
        current = await self.get_score(user_id)
        new_score = current + score_delta
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO user_feedback
                    (user_id, guild_id, score, violation_count,
                     last_violation_type, last_violation_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    score               = ?,
                    violation_count     = violation_count + 1,
                    last_violation_type = excluded.last_violation_type,
                    last_violation_at   = excluded.last_violation_at,
                    updated_at          = excluded.updated_at
                """,
                (user_id, guild_id, new_score, violation_type, now, now, new_score),
            )
            await db.commit()

        logger.info(
            f"유저 피드백 업데이트 — user={user_id} type={violation_type} "
            f"{current} +{score_delta} → {new_score}"
        )
        return new_score
