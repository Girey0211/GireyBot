"""
스킬 관리 명령어 정의

각 슬래시 명령어에 대응하는 관리 의도 키워드를 정의합니다.
라우터가 이 키워드를 참조하여 "스킬 실행"과 "스킬 관리" 요청을 구분합니다.

새로운 관리 명령어를 추가하면 여기에도 키워드를 등록하세요.
"""

# 슬래시 명령어 → 관리 의도 키워드 매핑
MANAGEMENT_ACTIONS: dict[str, list[str]] = {
    "/skills-create": ["만들어", "생성", "추가", "create", "add"],
    "/skills-edit": ["수정", "편집", "변경", "바꿔", "고쳐", "edit", "modify"],
    "/skills-delete": ["삭제", "지워", "지우", "없애", "제거", "delete", "remove"],
    "/skills-setup": ["설정", "세팅", "setup", "configure"],
    "/skills-info": ["정보", "알려줘", "info"],
    "/skills-reload": ["리로드", "reload", "새로고침"],
}


def get_management_keywords() -> tuple[str, ...]:
    """모든 관리 의도 키워드를 하나의 튜플로 반환합니다."""
    keywords = []
    for kws in MANAGEMENT_ACTIONS.values():
        keywords.extend(kws)
    return tuple(keywords)
