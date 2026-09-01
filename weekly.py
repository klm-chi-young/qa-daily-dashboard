import os
import sys
import json
from datetime import datetime, timedelta
from jira import JIRA

# ==========================================
# 1. 설정 정보
# ==========================================
CONFIG = {
    "QART_SPRINT": ["6643", "6645"],  # 필요시 단일("6643") 또는 리스트(["6643", "6645"]) 가능
}

JIRA_SERVER = 'https://pet-friends.atlassian.net'
# 보안을 위해 환경변수에서 읽어오고, 없을 경우 기본값 사용
JIRA_USER = os.getenv('JIRA_USER', 'cy.kim2@pet-friends.co.kr')
# 보안 필독: JIRA 토큰은 GitHub Secrets에 등록된 값을 환경변수로 불러옵니다.
JIRA_TOKEN = os.getenv('JIRA_API_TOKEN')

START_DATE_FIELDS = ['customfield_10015', 'customfield_10071', 'customfield_10145', 'customfield_10085', 'customfield_10115', 'customfield_10137']
DUE_DATE_FIELDS = ['duedate', 'customfield_10061', 'customfield_10083']
DEPLOY_DATE_FIELDS = ['customfield_10170', 'customfield_10203', 'customfield_10336']

# 📌 배포까지 완전 종료된 상태 (배포일 표기 대상)
DEPLOY_DONE_STATUSES = ['QA 완료(배포완료)', '배포 완료', '배포완료', 'Done', 'Closed', 'Resolved']

# 📌 QA는 완료되었으나 배포 대기 상태 (배포예정일 표기 대상)
QA_DONE_STATUSES = ['QA 진행완료', 'QA 진행 완료', 'QA진행완료', 'QA 완료']

# 📌 진행 중 / 진행 예정 상태
IN_PROGRESS_STATUSES = ['QA 진행중', 'QA 진행 중', 'TC 작성중', 'TC 작성 중', 'In Progress', '진행 중']
PLANNED_STATUSES = ['Request List', 'TC 작성완료', 'TC 작성 완료', 'To Do', 'Backlog']

def clean_sprint_str(val):
    return str(val).replace('"', '').replace("'", "").strip()

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

def format_short_date(date_obj):
    if not date_obj:
        return "미정"
    return f"{date_obj.month}/{date_obj.day}"

def find_assignee_and_participants(jira, issue):
    people = set()
    if issue.fields.assignee:
        people.add(str(issue.fields.assignee.displayName))
        
    for field_name, value in issue.raw['fields'].items():
        if field_name.startswith('customfield_') and value:
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and 'displayName' in item:
                        people.add(item['displayName'])
            elif isinstance(value, dict) and 'displayName' in value:
                if 'emailAddress' in value or 'accountType' in value:
                    people.add(value['displayName'])

    if not people:
        people.add("미지정")
        
    return list(people)

def get_weekly_dashboard_data():
    jira = JIRA({'server': JIRA_SERVER}, basic_auth=(JIRA_USER, JIRA_TOKEN))
    
    # 📌 6643, 6645 및 활성 스프린트 전체 통합 JQL 조회
    sprints = CONFIG["QART_SPRINT"]
    if isinstance(sprints, list):
        sprint_ids = ", ".join([clean_sprint_str(s) for s in sprints])
        sprint_condition = f"sprint in ({sprint_ids})"
    else:
        sprint_ids = clean_sprint_str(sprints)
        sprint_condition = f"sprint = {sprint_ids}"

    jql = f'project = "QART" AND ({sprint_condition} OR sprint in openSprints()) ORDER BY created DESC'
    print(f"🔍 [QART Sprint JQL 실행]: {jql}")
    
    try:
        issues = jira.enhanced_search_issues(jql, maxResults=False)
        print(f"📦 [Jira 파싱 완료]: 총 {len(issues)}개 이슈가 파싱되었습니다.")
    except Exception as e:
        print(f"❌ JQL 조회 에러: {e}")
        issues = []

    today = datetime.now().date()
    
    # 📌 주간 날짜 기준 계산
    meeting_monday = today + timedelta(days=(0 - today.weekday()) % 7)
    last_tuesday = meeting_monday - timedelta(days=6)  # 지난주 화요일
    last_sunday = meeting_monday - timedelta(days=1)   # 지난주 일요일
    
    this_monday = meeting_monday                        # 이번주 월요일
    this_sunday = meeting_monday + timedelta(days=6)    # 이번주 일요일
    
    # 📌 다음 주 금요일 날짜 계산 (진행 예정 필터링용)
    next_friday = this_monday + timedelta(days=11)

    team_data = {}
    
    for i in issues:
        workers = find_assignee_and_participants(jira, i)
        status = i.fields.status.name.strip()
        
        start_date_str = get_field_value(i.fields, START_DATE_FIELDS)
        due_date_str = get_field_value(i.fields, DUE_DATE_FIELDS)
        deploy_date_str = get_field_value(i.fields, DEPLOY_DATE_FIELDS)
        
        start_date = parse_date(start_date_str)
        due_date = parse_date(due_date_str)
        deploy_date = parse_date(deploy_date_str)

        target_plan_date = deploy_date or due_date or datetime(9999, 12, 31).date()

        # 📌 상태별 라벨 구분 (배포완료: 배포일 / 기타: 배포 예정일)
        if status in DEPLOY_DONE_STATUSES:
            badge_label = "배포일"
            date_str_val = format_short_date(deploy_date or due_date)
        else:
            badge_label = "배포 예정일"
            date_str_val = format_short_date(deploy_date or due_date)

        issue_info = {
            'key': i.key,
            'summary': i.fields.summary,
            'link': f"{JIRA_SERVER}/browse/{i.key}",
            'status': status,
            'deploy_date': deploy_date,
            'due_date': due_date,
            'plan_date': target_plan_date,
            'badge_label': badge_label,
            'date_str': date_str_val
        }
        
        is_deploy_done = status in DEPLOY_DONE_STATUSES
        is_qa_done = status in QA_DONE_STATUSES
        is_in_progress_status = status in IN_PROGRESS_STATUSES

        for person in workers:
            if person not in team_data:
                team_data[person] = {
                    'completed_last_week': [],
                    'in_progress': [],
                    'planned_this_week': [],
                }

            # 📌 1. 완료 섹션 분류
            if is_deploy_done:
                if deploy_date and (last_tuesday <= deploy_date <= last_sunday):
                    if issue_info not in team_data[person]['completed_last_week']:
                        team_data[person]['completed_last_week'].append(issue_info)
                elif not deploy_date:
                    if issue_info not in team_data[person]['completed_last_week']:
                        team_data[person]['completed_last_week'].append(issue_info)
            elif is_qa_done:
                if issue_info not in team_data[person]['completed_last_week']:
                    team_data[person]['completed_last_week'].append(issue_info)

            # 📌 2. 진행 중 섹션 분류
            elif is_in_progress_status:
                if issue_info not in team_data[person]['in_progress']:
                    team_data[person]['in_progress'].append(issue_info)

            # 📌 3. 진행 예정 섹션 분류 (다음 주 금요일까지만 작성)
            elif status in PLANNED_STATUSES or not (is_deploy_done or is_qa_done or is_in_progress_status):
                # 날짜가 존재하는 경우 다음 주 금요일 이내인지 검증 (시작일/기한/배포일 기준)
                check_date = start_date or deploy_date or due_date
                if not check_date or (check_date <= next_friday):
                    if issue_info not in team_data[person]['planned_this_week']:
                        team_data[person]['planned_this_week'].append(issue_info)

    for person in team_data:
        team_data[person]['completed_last_week'].sort(key=lambda x: x['deploy_date'] or datetime(9999, 12, 31).date())
        team_data[person]['in_progress'].sort(key=lambda x: x['plan_date'])
        team_data[person]['planned_this_week'].sort(key=lambda x: x['plan_date'])

    return team_data, last_tuesday, last_sunday, this_monday, this_sunday, next_friday

def build_weekly_html(team_data, last_tue, last_sun, this_mon, this_sun, next_fri):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    title_period = f"지난주 성과({last_tue.strftime('%m/%d')}~{last_sun.strftime('%m/%d')}, 월요일 제외) & 이번주 계획({this_mon.strftime('%m/%d')}~{this_sun.strftime('%m/%d')})"

    sorted_members = sorted(team_data.keys(), key=lambda x: (x == "미지정", x))

    member_cards_html = ""
    notion_full_text = f"📋 QART 팀 주간 회의록 ({title_period})\n\n"

    for idx, member in enumerate(sorted_members):
        if member == "미지정": continue
        data = team_data[member]

        def format_markdown_section(title, issue_list):
            sec_md = f"**{title}**\n"
            if issue_list:
                for item in issue_list:
                    sec_md += f"- **[{item['summary']}]({item['link']})** `{item['badge_label']} : {item['date_str']}`\n"
            sec_md += "\n"
            return sec_md

        member_notion_md = ""
        member_notion_md += format_markdown_section("완료", data['completed_last_week'])
        member_notion_md += format_markdown_section("진행중", data['in_progress'])
        member_notion_md += format_markdown_section(f"진행예정 (~{next_fri.strftime('%m/%d')})", data['planned_this_week'])

        notion_full_text += f"👤 {member}\n" + member_notion_md

        def render_links(issue_list):
            if not issue_list:
                return "<div style='color:#94a3b8; font-size:13px;'>- 없음</div>"
            html = "<ul style='margin-left:20px; font-size:13px; line-height:1.8; color:#334155;'>"
            for item in issue_list:
                html += f"""<li>
                    <a href='{item['link']}' target='_blank' style='color:#2563eb; font-weight:700; text-decoration:underline;'>{item['summary']}</a>
                    <span style='background:#f1f5f9; color:#475569; font-size:11px; padding:2px 6px; border-radius:4px; font-weight:600; margin-left:4px;'>{item['status']}</span>
                    <span style='background:#fee2e2; color:#dc2626; font-size:11px; padding:2px 6px; border-radius:4px; font-weight:700; margin-left:6px;'>{item['badge_label']} : {item['date_str']}</span>
                </li>"""
            html += "</ul>"
            return html

        member_cards_html += f"""
        <div style="background:white; border-radius:12px; border:1px solid #e2e8f0; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
            <div style="display:flex; justify-content:space-between; align-items:center; padding-bottom:10px; border-bottom:2px solid #f1f5f9; margin-bottom:14px;">
                <div style="font-size:18px; font-weight:700; color:#0f172a;">👤 {member}</div>
                <button class="member-copy-btn" id="btn_memberText_{idx}" onclick="copyTextToClipboard('memberText_{idx}', 'btn_memberText_{idx}')">📋 {member}님 목록 복사 (이름 제외)</button>
            </div>

            <div style="margin-bottom:14px;">
                <div style="font-size:14px; font-weight:700; color:#16a34a; margin-bottom:6px;">1. 완료</div>
                {render_links(data['completed_last_week'])}
            </div>

            <div style="margin-bottom:14px;">
                <div style="font-size:14px; font-weight:700; color:#dc2626; margin-bottom:6px;">2. 진행 중</div>
                {render_links(data['in_progress'])}
            </div>

            <div>
                <div style="font-size:14px; font-weight:700; color:#2563eb; margin-bottom:6px;">3. 진행 예정 (~{next_fri.strftime('%m/%d')} 금)</div>
                {render_links(data['planned_this_week'])}
            </div>

            <textarea id="memberText_{idx}" style="display:none;">{member_notion_md}</textarea>
        </div>
        """

    html = f"""<!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QART 팀 주간 회의록 (노션 전용 복사)</title>
        <link href="https://fonts.googleapis.com/css2?family=Pretendard:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Pretendard', sans-serif; }}
            body {{ background-color: #f1f5f9; color: #0f172a; padding: 24px; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            .header {{ background: #0f172a; color: white; padding: 20px 24px; border-radius: 12px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }}
            .title {{ font-size: 20px; font-weight: 700; }}
            .sub {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
            .copy-btn {{ background: #2563eb; color: white; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 700; font-size: 14px; cursor: pointer; transition: 0.2s; }}
            .copy-btn:hover {{ background: #1d4ed8; }}
            .member-copy-btn {{ background: #f1f5f9; color: #334155; border: 1px solid #cbd5e1; padding: 6px 12px; border-radius: 6px; font-weight: 600; font-size: 12px; cursor: pointer; transition: 0.2s; }}
            .member-copy-btn:hover {{ background: #e2e8f0; color: #0f172a; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <div class="title">📋 QART 팀 주간 회의록</div>
                    <div class="sub">{title_period} | 생성시각: {now}</div>
                </div>
                <button class="copy-btn" id="btn_notionFullText" onclick="copyTextToClipboard('notionFullText', 'btn_notionFullText')">📋 팀 전체 복사</button>
            </div>

            {member_cards_html}
        </div>

        <textarea id="notionFullText" style="display:none;">{notion_full_text}</textarea>

        <script>
            function copyTextToClipboard(elementId, btnId) {{
                const textStr = document.getElementById(elementId).value;
                
                navigator.clipboard.writeText(textStr).then(() => {{
                    const btn = document.getElementById(btnId);
                    if (btn) {{
                        const origText = btn.innerText;
                        btn.innerText = "✅ 복사 완료!";
                        btn.style.backgroundColor = "#16a34a";
                        btn.style.color = "#ffffff";
                        setTimeout(() => {{
                            btn.innerText = origText;
                            btn.style.backgroundColor = "";
                            btn.style.color = "";
                        }}, 1500);
                    }}
                }}).catch(err => {{
                    console.error('복사 에러:', err);
                }});
            }}
        </script>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    if not JIRA_TOKEN:
        raise ValueError("❌ JIRA_API_TOKEN 환경변수가 설정되어 있지 않습니다.")

    print("🚀 주간 회의록(노션용) 생성 시작...")
    data, l_tue, l_sun, t_mon, t_sun, n_fri = get_weekly_dashboard_data()
    html_out = build_weekly_html(data, l_tue, l_sun, t_mon, t_sun, n_fri)
    
    # 📌 weekly 폴더를 자동 생성하고 그 안에 index.html로 저장
    os.makedirs("weekly", exist_ok=True)
    with open("weekly/index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
        
    print("🎉 성공! weekly/index.html 생성 완료!")

def get_weekly_html():
    data, l_tue, l_sun, t_mon, t_sun, n_fri = get_weekly_dashboard_data()
    return build_weekly_html(data, l_tue, l_sun, t_mon, t_sun, n_fri)
