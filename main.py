import os
import sys
import json
import urllib.parse
from datetime import datetime, timedelta
from jira import JIRA

# ==========================================
# 1. 설정 정보
# ==========================================
CONFIG = {
    "REPORT_MONTH": "2026년 8월",
    "QART_SPRINT": "2942",  # 8월 QART 스프린트 ID
}

JIRA_SERVER = 'https://pet-friends.atlassian.net'
JIRA_USER = 'cy.kim2@pet-friends.co.kr'

# 📌 깃허브 Secrets에 설정된 JIRA_API_TOKEN 환경변수를 읽어옵니다.
JIRA_TOKEN = os.environ.get('JIRA_API_TOKEN', '')

# 토큰이 비어있으면 에러를 발생시켜 실행을 멈추게 함
if not JIRA_TOKEN:
    raise ValueError("❌ JIRA_API_TOKEN 환경변수를 찾을 수 없습니다. GitHub Secrets 설정을 확인하세요.")

# 커스텀 필드 ID 후보군
START_DATE_FIELDS = ['customfield_10015', 'customfield_10071', 'customfield_10145', 'customfield_10085', 'customfield_10115', 'customfield_10137']
DUE_DATE_FIELDS = ['duedate', 'customfield_10061', 'customfield_10083']
DEPLOY_DATE_FIELDS = ['customfield_10170', 'customfield_10203', 'customfield_10336']

# 완료 상태 정의
DONE_STATUSES = ['QA 완료', '배포 완료', 'QA 완료(배포완료)', 'Done', 'Closed', 'Resolved']

# 요일 한글 매핑 딕셔너리
WEEKDAY_KOR = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']

# ==========================================
# 2. 날짜 파싱 및 커스텀 필드 추출 함수
# ==========================================
def get_field_value(issue_fields, field_id_list):
    for fid in field_id_list:
        val = getattr(issue_fields, fid, None)
        if val:
            val_str = str(val)
            if 'T' in val_str:
                val_str = val_str.split('T')[0]
            return val_str
    return None

def parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], '%Y-%m-%d').date()
    except Exception:
        return None

# ==========================================
# 3. Jira 데이터 추출 및 팀원별 분류 로직
# ==========================================
def get_team_dashboard_data():
    jira = JIRA({'server': JIRA_SERVER}, basic_auth=(JIRA_USER, JIRA_TOKEN))
    
    jql = f'project = "QART" AND sprint = {CONFIG["QART_SPRINT"]} ORDER BY created DESC'
    print(f"🔍 [QART Sprint {CONFIG['QART_SPRINT']}] 데이터 추출 중...")
    
    issues = jira.enhanced_search_issues(jql, maxResults=False)
    
    # 기준 날짜 계산
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    
    this_week_end = today + timedelta(days=(6 - today.weekday()))
    next_week_start = this_week_end + timedelta(days=1)

    team_data = {}
    
    for i in issues:
        assignee = str(i.fields.assignee.displayName) if i.fields.assignee else "미지정"
        status = i.fields.status.name
        
        start_date_str = get_field_value(i.fields, START_DATE_FIELDS)
        due_date_str = get_field_value(i.fields, DUE_DATE_FIELDS)
        deploy_date_str = get_field_value(i.fields, DEPLOY_DATE_FIELDS)
        
        start_date = parse_date(start_date_str)
        due_date = parse_date(due_date_str)
        deploy_date = parse_date(deploy_date_str)
        
        # 7월 이전 과거 종료건 필터링
        if due_date and due_date < parse_date("2026-08-01"):
            continue

        if assignee not in team_data:
            team_data[assignee] = {
                'today_deploy': [],    # 1. 🚀 오늘 배포 예정
                'today_progress': [],  # 2. 🔴 오늘 진행 중
                'tomorrow_plan': [],   # 3. 🟠 내일 진행 예정
                'next_week': [],       # 4. 🟡 다음 주 예정 업무
                'no_date': [],         # 5. ⚪ 날짜 미정 업무
                'total_count': 0
            }
            
        issue_info = {
            'key': i.key,
            'summary': i.fields.summary,
            'status': status,
            'start_date': start_date_str or '미정',
            'due_date': due_date_str or '미정',
            'deploy_date': deploy_date_str or '미정',
            'link': f"{JIRA_SERVER}/browse/{i.key}"
        }
        
        # 📌 [분류 로직]
        
        # 1. 오늘 배포 예정 (배포일이 오늘인 경우)
        if deploy_date and deploy_date == today:
            team_data[assignee]['today_deploy'].append(issue_info)

        # 이미 완료(QA 완료 등)된 티켓인지 확인
        is_done = status in DONE_STATUSES or any(k in status for k in ['완료', 'Done', 'Closed'])

        # 완료된 이슈는 배포 예정 이외의 섹션에는 노출하지 않음
        if not is_done:
            # 2. 오늘 진행 중 (시작일 ~ 기한 범위 내 오늘이 속한 경우)
            is_today_progress = False
            if start_date and due_date and (start_date <= today <= due_date):
                is_today_progress = True
            elif start_date == today or due_date == today:
                is_today_progress = True

            if is_today_progress:
                team_data[assignee]['today_progress'].append(issue_info)

            # 3. 내일 진행 예정
            is_tomorrow = False
            if start_date == tomorrow or due_date == tomorrow:
                is_tomorrow = True
            elif start_date and due_date and (start_date <= tomorrow <= due_date):
                is_tomorrow = True

            if is_tomorrow:
                team_data[assignee]['tomorrow_plan'].append(issue_info)

            # 4. 다음 주 예정 업무
            is_next_week = False
            if (start_date and start_date >= next_week_start) or (due_date and due_date >= next_week_start):
                is_next_week = True

            if is_next_week:
                team_data[assignee]['next_week'].append(issue_info)

            # 5. 시작일/기한 미정 업무
            if not start_date and not due_date and not deploy_date:
                team_data[assignee]['no_date'].append(issue_info)
            elif not is_today_progress and not is_tomorrow and not is_next_week and (deploy_date != today):
                team_data[assignee]['no_date'].append(issue_info)

        team_data[assignee]['total_count'] += 1

    print(f"✅ 총 {len(team_data)}개 담당 그룹 데이터 분류 완료!")
    return team_data, today, tomorrow, next_week_start

# ==========================================
# 4. HTML 팀 대시보드 생성 로직
# ==========================================
def build_html(team_data, today, tomorrow, next_start):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # 📌 [오늘의 일자 및 요일 생성] 예: 2026년 08월 13일 목요일
    today_weekday = WEEKDAY_KOR[today.weekday()]
    full_title_date = f"{today.strftime('%Y년 %m월 %d일')} {today_weekday}"
    
    # 중복 제거된 오늘 진행 중/배포 티켓 수
    today_active_keys = set()
    for data in team_data.values():
        for item in data['today_deploy']:
            today_active_keys.add(item['key'])
        for item in data['today_progress']:
            today_active_keys.add(item['key'])
            
    active_in_progress_count = len(today_active_keys)
    total_assigned_issues = sum(data['total_count'] for data in team_data.values())

    sorted_members = sorted(
        team_data.keys(),
        key=lambda x: (x == "미지정", x)
    )

    member_cards_html = ""
    for member in sorted_members:
        data = team_data[member]
        
        card_border_style = "border:1px dashed #cbd5e1; background:#f8fafc;" if member == "미지정" else "border:1px solid #e2e8f0; background:white;"
        avatar_bg = "#94a3b8" if member == "미지정" else "#2563eb"

        def render_issue_list(issue_list, badge_color="#2563eb"):
            if not issue_list:
                return "<div style='color:#94a3b8; font-size:12px; padding:6px 0;'>할당된 업무 없음</div>"
            
            html = ""
            for item in issue_list:
                status_bg = "#dcfce7" if "완료" in item['status'] else "#e2e8f0"
                status_color = "#15803d" if "완료" in item['status'] else "#475569"
                
                html += f"""
                <div style="background:#ffffff; border:1px solid #e2e8f0; padding:10px 12px; border-radius:8px; margin-bottom:8px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                        <a href="{item['link']}" target="_blank" style="font-weight:700; color:{badge_color}; text-decoration:none; font-size:12px;">{item['key']}</a>
                        <span style="font-size:11px; background:{status_bg}; color:{status_color}; padding:2px 6px; border-radius:4px; font-weight:600;">{item['status']}</span>
                    </div>
                    <div style="font-size:13px; font-weight:600; color:#1e293b; margin-bottom:6px; line-height:1.3;">{item['summary']}</div>
                    <div style="display:flex; gap:8px; font-size:11px; color:#64748b; flex-wrap:wrap;">
                        <span>🛫 시작: <strong>{item['start_date']}</strong></span>
                        <span>📅 기한: <strong>{item['due_date']}</strong></span>
                        <span>🚀 배포: <strong style="color:#dc2626;">{item['deploy_date']}</strong></span>
                    </div>
                </div>
                """
            return html

        member_active_keys = set([i['key'] for i in data['today_deploy']] + [i['key'] for i in data['today_progress']])
        member_active_count = len(member_active_keys)

        member_cards_html += f"""
        <div style="border-radius:14px; {card_border_style} padding:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05); grid-column: span 6;">
            <!-- 팀원 헤더 -->
            <div style="display:flex; justify-content:space-between; align-items:center; padding-bottom:12px; border-bottom:2px solid #f1f5f9; margin-bottom:16px;">
                <div style="display:flex; align-items:center; gap:10px;">
                    <div style="width:36px; height:36px; background:{avatar_bg}; color:white; font-weight:700; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:15px;">
                        {member[0]}
                    </div>
                    <div>
                        <div style="font-size:16px; font-weight:700; color:#0f172a;">{member}</div>
                        <div style="font-size:12px; color:#64748b;">
                            오늘 진행 중: <strong style="color:#dc2626;">{member_active_count}개</strong> 
                            <span style="color:#cbd5e1;">|</span> 전체: {data['total_count']}개
                        </div>
                    </div>
                </div>
            </div>

            <!-- 1. 오늘 배포 예정 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:13px; font-weight:700; color:#059669; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                    🚀 오늘 배포 예정 (배포일: {today.strftime('%m/%d')} : {len(data['today_deploy'])})
                </div>
                {render_issue_list(data['today_deploy'], "#059669")}
            </div>

            <!-- 2. 오늘 진행 중 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:13px; font-weight:700; color:#dc2626; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                    🔴 오늘 진행 중 ({today.strftime('%m/%d')} : {len(data['today_progress'])})
                </div>
                {render_issue_list(data['today_progress'], "#dc2626")}
            </div>

            <!-- 3. 내일 진행 예정 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:13px; font-weight:700; color:#d97706; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                    🟠 내일 진행 예정 ({tomorrow.strftime('%m/%d')} : {len(data['tomorrow_plan'])})
                </div>
                {render_issue_list(data['tomorrow_plan'], "#d97706")}
            </div>

            <!-- 4. 다음 주 예정 업무 -->
            <div style="margin-bottom:16px;">
                <div style="font-size:13px; font-weight:700; color:#2563eb; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                    🟡 다음 주 예정 업무 ({next_start.strftime('%m/%d')} ~ : {len(data['next_week'])})
                </div>
                {render_issue_list(data['next_week'], "#2563eb")}
            </div>

            <!-- 5. 시작일/기한 미정 업무 -->
            <div>
                <div style="font-size:13px; font-weight:700; color:#64748b; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                    ⚪ 시작일/기한 미정 업무 ({len(data['no_date'])})
                </div>
                {render_issue_list(data['no_date'], "#64748b")}
            </div>
        </div>
        """

    real_member_count = len([m for m in team_data.keys() if m != "미지정"])

    html = f"""<!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{full_title_date} QART 팀원별 데일리 대시보드</title>
        <link href="https://fonts.googleapis.com/css2?family=Pretendard:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Pretendard', sans-serif; }}
            body {{ background-color: #f1f5f9; color: #0f172a; padding: 24px; }}
            .container {{ max-width: 1400px; margin: 0 auto; display: grid; grid-template-columns: repeat(12, 1fr); gap: 20px; }}
            
            .header {{ grid-column: span 12; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: white; padding: 24px 30px; border-radius: 16px; display: flex; justify-content: space-between; align-items: center; }}
            .title {{ font-size: 22px; font-weight: 700; }}
            .sub-info {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
            
            .summary-bar {{ grid-column: span 12; display: flex; gap: 15px; background: white; padding: 16px 20px; border-radius: 12px; border: 1px solid #e2e8f0; }}
            .summary-item {{ flex: 1; text-align: center; border-right: 1px solid #f1f5f9; }}
            .summary-item:last-child {{ border-right: none; }}
            .summary-label {{ font-size: 12px; color: #64748b; font-weight: 600; }}
            .summary-val {{ font-size: 22px; font-weight: 800; color: #dc2626; margin-top: 2px; }}

            @media (max-width: 1024px) {{
                .container > div {{ grid-column: span 12 !important; }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- 📌 제목 부분에 일자 및 요일 반영 -->
            <div class="header">
                <div>
                    <div class="title">👥 QART 팀원별 데일리 스크럼 대시보드 ({full_title_date})</div>
                    <div class="sub-info">오늘 배포 / 데일리 진행 / 내일 & 차주 일정 관리</div>
                </div>
                <div style="text-align:right; font-size:12px; color:#94a3b8;">
                    업데이트: {now}
                </div>
            </div>

            <div class="summary-bar">
                <div class="summary-item">
                    <div class="summary-label">담당 팀원</div>
                    <div class="summary-val" style="color:#0f172a;">{real_member_count}명</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">진행 중 티켓</div>
                    <div class="summary-val">{active_in_progress_count}개</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">인당 평균 진행 티켓</div>
                    <div class="summary-val" style="color:#2563eb;">{round(active_in_progress_count/real_member_count, 1) if real_member_count else 0}개</div>
                </div>
                <div class="summary-item">
                    <div class="summary-label">스프린트 전체 티켓</div>
                    <div class="summary-val" style="color:#64748b;">{total_assigned_issues}개</div>
                </div>
            </div>

            {member_cards_html}
        </div>
    </body>
    </html>
    """
    return html

# ==========================================
# 5. 실행부
# ==========================================
if __name__ == "__main__":
    # 📌 1. 지라 토큰이 제대로 전달되었는지 검증
    if not JIRA_TOKEN or JIRA_TOKEN == 'YOUR_LOCAL_TOKEN_FOR_TEST':
        raise ValueError("❌ JIRA_API_TOKEN 이 깃허브 Secrets에 설정되지 않았거나 잘못 전달되었습니다!")

    print("🚀 지라 데이터 추출 및 대시보드 생성 시작...")
    
    # 📌 2. try-except 제거하여 에러 발생 시 깃허브 액션이 원인을 명확히 로그에 출력하도록 함
    data, today, tomorrow, next_start = get_team_dashboard_data()
    html_out = build_html(data, today, tomorrow, next_start)
    
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
        
    print("🎉 성공! index.html 파일이 정상적으로 생성되었습니다.")
