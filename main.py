import os
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from jira import JIRA

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

JIRA_SERVER = 'https://pet-friends.atlassian.net'
JIRA_USER = 'cy.kim2@pet-friends.co.kr'

# 🔒 보안 처리: API 토큰을 소스코드에 남기지 않고 GitHub Secrets / 환경변수에서만 불러옵니다.
JIRA_TOKEN = os.environ.get('JIRA_API_TOKEN')

TEAM_CALENDAR_EMAILS = {
    "리암(Liam/김치영)": "cy.kim2@pet-friends.co.kr",
    "베리(Berry/강샛별)": "sb.kang@pet-friends.co.kr",
    "솔릭(Solric/구건모)": "gm.koo@pet-friends.co.kr",
    "하퍼(Harper/이하경)": "hk.lee@pet-friends.co.kr",
}

START_DATE_FIELDS = ['customfield_10015', 'customfield_10071', 'customfield_10145', 'customfield_10085', 'customfield_10115', 'customfield_10137']
DUE_DATE_FIELDS = ['duedate', 'customfield_10061', 'customfield_10083']
DEPLOY_DATE_FIELDS = ['customfield_10170', 'customfield_10203', 'customfield_10336']

DONE_STATUSES = ['QA 완료', '배포 완료', 'QA 완료(배포완료)', 'Done', 'Closed', 'Resolved']
WEEKDAY_KOR = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']

KST = timezone(timedelta(hours=9))
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def get_calendar_service():
    creds = None
    token_json_env = os.environ.get('GOOGLE_TOKEN_JSON')
    if token_json_env:
        with open('token.json', 'w', encoding='utf-8') as f:
            f.write(token_json_env)

    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists('credentials.json'):
                print("⚠️ credentials.json 키 파일이 없습니다.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
        with open('token.json', 'w', encoding='utf-8') as token:
            token.write(creds.to_json())
            
    return build('calendar', 'v3', credentials=creds)

def fetch_today_meetings(service, calendar_email, today_date):
    if not service or not calendar_email:
        return []
    start_dt = datetime(today_date.year, today_date.month, today_date.day, 0, 0, 0, tzinfo=KST).isoformat()
    end_dt = datetime(today_date.year, today_date.month, today_date.day, 23, 59, 59, tzinfo=KST).isoformat()
    try:
        events_result = service.events().list(
            calendarId=calendar_email,
            timeMin=start_dt,
            timeMax=end_dt,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        items = events_result.get('items', [])
        meetings = []
        for item in items:
            summary = item.get('summary', '(제목 없음)')
            start = item['start'].get('dateTime', item['start'].get('date'))
            end = item['end'].get('dateTime', item['end'].get('date'))
            if 'T' in start:
                start_time = start.split('T')[1][:5]
                end_time = end.split('T')[1][:5] if 'T' in end else ''
                time_str = f"{start_time} ~ {end_time}"
            else:
                time_str = "종일 일정"
            meetings.append({'summary': summary, 'time': time_str})
        return meetings
    except Exception as e:
        print(f"❌ [{calendar_email}] 캘린더 읽기 에러: {e}")
        return []

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

def get_team_dashboard_data():
    if not JIRA_TOKEN:
        raise ValueError("JIRA_API_TOKEN 환경 변수가 설정되지 않았습니다. GitHub Secrets를 확인해 주세요.")

    jira = JIRA({'server': JIRA_SERVER}, basic_auth=(JIRA_USER, JIRA_TOKEN))
    cal_service = get_calendar_service()
    
    # 📌 현재 활성화된(openSprints) 스프린트 자동 탐색 조건
    jql = 'project = "QART" AND sprint in openSprints() ORDER BY created DESC'
    print(f"🔍 [QART 진행 중인 스프린트] 데이터 추출 중...")
    
    issues = jira.enhanced_search_issues(jql, maxResults=False)
    
    # 활성 스프린트 검색 결과가 없을 경우 최신 QART 이슈 50개 파싱
    if not issues:
        print("⚠️ 진행 중인 스프린트 조건 결과가 없어 최근 생성된 QART 이슈를 파싱합니다.")
        jql = 'project = "QART" ORDER BY created DESC'
        issues = jira.enhanced_search_issues(jql, maxResults=50)

    print(f"📦 [Jira 추출 완료]: 총 {len(issues)}개 이슈 파싱됨")
    
    today = datetime.now(KST).date()
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

        if assignee not in team_data:
            cal_email = TEAM_CALENDAR_EMAILS.get(assignee, None)
            
            if "리암" in assignee or cal_email == "cy.kim2@pet-friends.co.kr":
                cal_email = 'primary'

            today_meetings = fetch_today_meetings(cal_service, cal_email, today)

            team_data[assignee] = {
                'today_meetings': today_meetings,
                'today_deploy': [],
                'today_progress': [],
                'tomorrow_plan': [],
                'next_week': [],
                'no_date': [],
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
        
        if deploy_date and deploy_date == today:
            team_data[assignee]['today_deploy'].append(issue_info)

        is_done = status in DONE_STATUSES or any(k in status for k in ['완료', 'Done', 'Closed'])

        if not is_done:
            is_today_progress = False
            if start_date and due_date and (start_date <= today <= due_date):
                is_today_progress = True
            elif start_date == today or due_date == today:
                is_today_progress = True

            if is_today_progress:
                team_data[assignee]['today_progress'].append(issue_info)

            is_tomorrow = False
            if start_date == tomorrow or due_date == tomorrow:
                is_tomorrow = True
            elif start_date and due_date and (start_date <= tomorrow <= due_date):
                is_tomorrow = True

            if is_tomorrow:
                team_data[assignee]['tomorrow_plan'].append(issue_info)

            is_next_week = False
            if (start_date and start_date >= next_week_start) or (due_date and due_date >= next_week_start):
                is_next_week = True

            if is_next_week:
                team_data[assignee]['next_week'].append(issue_info)

            if not start_date and not due_date and not deploy_date:
                team_data[assignee]['no_date'].append(issue_info)
            elif not is_today_progress and not is_tomorrow and not is_next_week and (deploy_date != today):
                team_data[assignee]['no_date'].append(issue_info)

        team_data[assignee]['total_count'] += 1

    print(f"✅ 총 {len(team_data)}개 담당 그룹 데이터 분류 완료!")
    return team_data, today, tomorrow, next_week_start

def build_html(team_data, today, tomorrow, next_start):
    now = datetime.now(KST).strftime('%Y-%m-%d %H:%M')
    
    today_weekday = WEEKDAY_KOR[today.weekday()]
    full_title_date = f"{today.strftime('%Y년 %m월 %d일')} {today_weekday}"
    
    today_active_keys = set()
    for data in team_data.values():
        for item in data['today_deploy']:
            today_active_keys.add(item['key'])
        for item in data['today_progress']:
            today_active_keys.add(item['key'])
            
    active_in_progress_count = len(today_active_keys)
    total_assigned_issues = sum(data['total_count'] for data in team_data.values())

    sorted_members = sorted(team_data.keys(), key=lambda x: (x == "미지정", x))

    member_cards_html = ""
    for member in sorted_members:
        data = team_data[member]
        
        card_border_style = "border:1px dashed #cbd5e1; background:#f8fafc;" if member == "미지정" else "border:1px solid #e2e8f0; background:white;"
        avatar_bg = "#94a3b8" if member == "미지정" else "#2563eb"

        def render_meeting_list(meeting_list):
            if not meeting_list:
                return "<div style='color:#94a3b8; font-size:12px; padding:4px 0;'>오늘 예정된 회의 없음</div>"
            html = "<div style='display:flex; flex-direction:column; gap:6px;'>"
            for m in meeting_list:
                html += f"""
                <div style="background:#f1f5f9; border-left:3px solid #8b5cf6; padding:6px 10px; border-radius:4px; font-size:12px;">
                    <span style="font-weight:700; color:#6d28d9;">[{m['time']}]</span>
                    <span style="font-weight:600; color:#1e293b; margin-left:6px;">{m['summary']}</span>
                </div>
                """
            html += "</div>"
            return html

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

            <!-- 0. 📅 오늘 예정된 회의 -->
            <div style="margin-bottom:16px; background:#f8fafc; padding:10px 12px; border-radius:8px; border:1px solid #e2e8f0;">
                <div style="font-size:13px; font-weight:700; color:#7c3aed; margin-bottom:8px; display:flex; align-items:center; gap:6px;">
                    📅 오늘 예정된 회의 ({len(data['today_meetings'])})
                </div>
                {render_meeting_list(data['today_meetings'])}
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
            <div class="header">
                <div>
                    <div class="title">👥 QART 팀원별 데일리 스크럼 대시보드 ({full_title_date})</div>
                    <div class="sub-info">오늘 회의 / 오늘 배포 / 데일리 진행 관리</div>
                </div>
                <div style="text-align:right; font-size:12px; color:#94a3b8;">
                    업데이트: {now} (KST)
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

if __name__ == "__main__":
    try:
        data, today, tomorrow, next_start = get_team_dashboard_data()
        html_out = build_html(data, today, tomorrow, next_start)
        file_name = "index.html"
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"\n🎉 성공! 데일리 현황판이 생성되었습니다: {os.path.abspath(file_name)}")
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
