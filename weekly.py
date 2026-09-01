import os
import sys
import json
import re
from datetime import datetime, timedelta, date
from jira import JIRA

# ==========================================
# 1. 설정 정보
# ==========================================
CONFIG = {
    "QART_SPRINT": ["6643", "6645"],  # QART 기본 스프린트 ID
    "HOTFIX_SPRINT": "6741",         # QA 프로젝트 핫픽스 스프린트 ID (월별 변경)
}

JIRA_SERVER = 'https://pet-friends.atlassian.net'
JIRA_USER = os.getenv('JIRA_USER', 'cy.kim2@pet-friends.co.kr')
JIRA_TOKEN = os.getenv('JIRA_API_TOKEN')

START_DATE_FIELDS = ['customfield_10015', 'customfield_10071', 'customfield_10145', 'customfield_10085', 'customfield_10115', 'customfield_10137']
DUE_DATE_FIELDS = ['duedate', 'customfield_10061', 'customfield_10083']
DEPLOY_DATE_FIELDS = ['customfield_10170', 'customfield_10203', 'customfield_10336']

DEPLOY_DONE_STATUSES = ['QA 완료(배포완료)', '배포 완료', '배포완료', 'Done', 'Closed', 'Resolved']
QA_DONE_STATUSES = ['QA 진행완료', 'QA 진행 완료', 'QA진행완료', 'QA 완료']
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
        val_str = str(date_str)
        if 'T' in val_str:
            val_str = val_str.split('T')[0]
        return datetime.strptime(val_str[:10], '%Y-%m-%d').date()
    except Exception:
        return None

def format_short_date(date_obj):
    if not date_obj:
        return "미정"
    return f"{date_obj.month}/{date_obj.day}"

def find_assignee_and_participants(jira, issue):
    """일반 이슈용: 담당자 + 참여자 목록 추출"""
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

def extract_text_from_adf(data):
    if isinstance(data, str):
        return data
    elif isinstance(data, dict):
        text = ""
        if 'text' in data:
            text += data['text']
        if 'content' in data and isinstance(data['content'], list):
            for child in data['content']:
                text += extract_text_from_adf(child) + "\n"
        return text.strip()
    elif isinstance(data, list):
        return "\n".join([extract_text_from_adf(item) for item in data]).strip()
    return str(data) if data else ""

def parse_hotfix_reason_details(raw_text):
    cause, action, tc_exists = "", "", ""
    if not raw_text:
        return cause, action, tc_exists

    clean_text = raw_text.replace('\r', '')

    cause_match = re.search(r'(?:이슈\s*원인|원인)\s*[:\*]*\s*(.*?)(?=(?:대응|TC|TC\s*존재|$))', clean_text, re.DOTALL | re.IGNORECASE)
    action_match = re.search(r'대응\s*[:\*]*\s*(.*?)(?=(?:TC|TC\s*존재|$))', clean_text, re.DOTALL | re.IGNORECASE)
    tc_match = re.search(r'TC\s*존재\s*여부\s*[:\*]*\s*(.*)', clean_text, re.DOTALL | re.IGNORECASE)

    if cause_match:
        cause = cause_match.group(1).strip(' *:\n\t')
    if action_match:
        action = action_match.group(1).strip(' *:\n\t')
    if tc_match:
        tc_exists = tc_match.group(1).strip(' *:\n\t')

    if not (cause or action or tc_exists):
        cause = raw_text.strip()

    return cause, action, tc_exists

def get_hotfix_reason(issue, reason_field_ids):
    raw_fields = issue.raw.get('fields', {})
    for fid in reason_field_ids:
        val = raw_fields.get(fid)
        if val:
            res_text = extract_text_from_adf(val)
            if res_text:
                return res_text.strip()
    return ""

def get_weekly_dashboard_data():
    jira = JIRA({'server': JIRA_SERVER}, basic_auth=(JIRA_USER, JIRA_TOKEN))
    
    reason_field_ids = []
    try:
        all_fields = jira.fields()
        reason_field_ids = [f['id'] for f in all_fields if '사유' in f['name']]
    except Exception as e:
        print(f"⚠️ 사유 필드 목록 조회 실패: {e}")

    today = datetime.now().date()
    
    # 📌 [새로운 날짜 기준일 설정: 화요일 ~ 차주 월요일]
    # 오늘(화요일)부터 차주 월요일까지 계산
    # 요일 코드: 월=0, 화=1, 수=2, 목=3, 금=4, 토=5, 일=6
    offset_to_tuesday = (today.weekday() - 1) % 7
    target_tuesday = today - timedelta(days=offset_to_tuesday)   # 가장 최근 화요일 (9/1)
    target_next_monday = target_tuesday + timedelta(days=6)      # 차주 월요일 (9/7)
    
    # 진행 예정 마감일 (~ 다다음주 금요일 9/11)
    this_monday = today - timedelta(days=today.weekday())
    next_friday = this_monday + timedelta(days=11)

    # 1. 메인 QART 프로젝트 JQL
    sprints = CONFIG["QART_SPRINT"]
    if isinstance(sprints, list):
        sprint_ids = ", ".join([clean_sprint_str(s) for s in sprints])
        sprint_condition = f"sprint in ({sprint_ids})"
    else:
        sprint_ids = clean_sprint_str(sprints)
        sprint_condition = f"sprint = {sprint_ids}"

    main_jql = f'project = "QART" AND ({sprint_condition} OR sprint in openSprints()) ORDER BY created DESC'
    
    # 2. QA 프로젝트 핫픽스 전용 JQL
    hotfix_sprint = clean_sprint_str(CONFIG["HOTFIX_SPRINT"])
    hotfix_jql = f'project = "QA" AND sprint = {hotfix_sprint}'

    # 3. 버그 이슈 전용 JQL (화요일 ~ 차주 월요일 사이 작성된 버그만)
    bug_jql = f'project in ("QART", "QA") AND type = Bug AND created >= "{target_tuesday.strftime("%Y-%m-%d")}" AND created <= "{target_next_monday.strftime("%Y-%m-%d")}" ORDER BY created DESC'

    print(f"🔍 [QART 메인 JQL 실행]: {main_jql}")
    print(f"🔥 [QA 핫픽스 JQL 실행]: {hotfix_jql}")
    print(f"🐛 [버그 이슈 JQL 실행]: {bug_jql}")
    
    issues = []
    try:
        main_issues = jira.enhanced_search_issues(main_jql, maxResults=False)
        issues.extend(main_issues)
    except Exception as e:
        print(f"❌ 메인 JQL 조회 에러: {e}")

    try:
        hotfix_issues = jira.enhanced_search_issues(hotfix_jql, maxResults=False)
        issues.extend(hotfix_issues)
    except Exception as e:
        print(f"❌ 핫픽스 JQL 조회 에러: {e}")

    try:
        bug_issues = jira.enhanced_search_issues(bug_jql, maxResults=False)
        issues.extend(bug_issues)
    except Exception as e:
        print(f"❌ 버그 JQL 조회 에러: {e}")

    team_data = {}

    def ensure_person(person_name):
        if person_name not in team_data:
            team_data[person_name] = {
                'hotfixes': [],
                'completed_last_week': [],
                'in_progress': [],
                'planned_this_week': [],
                'bugs': []
            }

    for i in issues:
        status = i.fields.status.name.strip()
        issue_type = str(getattr(i.fields.issuetype, 'name', ''))
        
        start_date_str = get_field_value(i.fields, START_DATE_FIELDS)
        due_date_str = get_field_value(i.fields, DUE_DATE_FIELDS)
        deploy_date_str = get_field_value(i.fields, DEPLOY_DATE_FIELDS)
        
        start_date = parse_date(start_date_str)
        due_date = parse_date(due_date_str)
        deploy_date = parse_date(deploy_date_str)
        created_date = parse_date(str(i.fields.created))
        updated_date = parse_date(str(i.fields.updated))

        is_bug = ("Bug" in issue_type) or ("버그" in issue_type)
        is_hotfix = not is_bug and ((i.fields.project.key == "QA") or ("Hotfix" in issue_type) or ("핫픽스" in issue_type))

        raw_reason = get_hotfix_reason(i, reason_field_ids) if is_hotfix else ""
        cause, action, tc_exists = parse_hotfix_reason_details(raw_reason)

        reporter_name = str(i.fields.reporter.displayName) if i.fields.reporter else "미지정"

        target_plan_date = deploy_date or due_date or datetime(9999, 12, 31).date()

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
            'created_date': created_date,
            'plan_date': target_plan_date,
            'badge_label': badge_label,
            'date_str': date_str_val,
            'is_hotfix': is_hotfix,
            'is_bug': is_bug,
            'cause': cause,
            'action': action,
            'tc_exists': tc_exists
        }
        
        is_deploy_done = status in DEPLOY_DONE_STATUSES
        is_qa_done = status in QA_DONE_STATUSES
        is_in_progress_status = status in IN_PROGRESS_STATUSES

        # 📌 1) 버그/이슈티켓 (Bug): 화요일 ~ 차주 월요일 사이 작성된 건만 매칭
        if is_bug:
            ensure_person(reporter_name)
            if created_date and (target_tuesday <= created_date <= target_next_monday):
                if issue_info not in team_data[reporter_name]['bugs']:
                    team_data[reporter_name]['bugs'].append(issue_info)
            continue

        # 📌 2) 핫픽스 (Hotfix): QA 프로젝트 보고자(Reporter) 기준 매칭
        if is_hotfix:
            ensure_person(reporter_name)
            if issue_info not in team_data[reporter_name]['hotfixes']:
                team_data[reporter_name]['hotfixes'].append(issue_info)
            continue

        # 📌 3) 1~3번 일반 스프린트 이슈: 담당자(Assignee) / 참여자 기준 매칭
        workers = find_assignee_and_participants(jira, i)
        for person in workers:
            ensure_person(person)
            if is_deploy_done or is_qa_done:
                # 🎯 화요일 ~ 차주 월요일 사이 배포/완료된 건만 1. 완료에 포함
                check_finish_date = deploy_date or due_date or updated_date
                if check_finish_date and (target_tuesday <= check_finish_date <= target_next_monday):
                    if issue_info not in team_data[person]['completed_last_week']:
                        team_data[person]['completed_last_week'].append(issue_info)

            elif is_in_progress_status:
                # 🎯 화요일 ~ 차주 월요일 사이 활동(작성일/업데이트일)된 진행 중 건만 포함
                check_active_date = updated_date or created_date
                if check_active_date and (target_tuesday <= check_active_date <= target_next_monday):
                    if issue_info not in team_data[person]['in_progress']:
                        team_data[person]['in_progress'].append(issue_info)

            elif status in PLANNED_STATUSES or not (is_deploy_done or is_qa_done or is_in_progress_status):
                check_date = start_date or deploy_date or due_date
                if not check_date or (check_date <= next_friday):
                    if issue_info not in team_data[person]['planned_this_week']:
                        team_data[person]['planned_this_week'].append(issue_info)

    for person in team_data:
        team_data[person]['hotfixes'].sort(key=lambda x: x['deploy_date'] or datetime(9999, 12, 31).date())
        team_data[person]['completed_last_week'].sort(key=lambda x: x['deploy_date'] or datetime(9999, 12, 31).date())
        team_data[person]['in_progress'].sort(key=lambda x: x['plan_date'])
        team_data[person]['planned_this_week'].sort(key=lambda x: x['plan_date'])
        team_data[person]['bugs'].sort(key=lambda x: x['created_date'] or datetime(9999, 12, 31).date(), reverse=True)

    return team_data, target_tuesday, target_next_monday, target_tuesday, target_next_monday, next_friday

def build_weekly_html(team_data, target_tue, target_next_mon, this_mon, this_sun, next_fri):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    title_period = f"이번주 주간 보고({target_tue.strftime('%m/%d')}~{target_next_mon.strftime('%m/%d')}) & 계획(~{next_fri.strftime('%m/%d')})"

    sorted_members = sorted(team_data.keys(), key=lambda x: (x == "미지정", x))

    member_cards_html = ""
    notion_full_text = f"📋 QART 팀 주간 회의록 ({title_period})\n\n"

    for idx, member in enumerate(sorted_members):
        if member == "미지정": continue
        data = team_data[member]

        def format_markdown_section(title, issue_list):
            count = len(issue_list)
            sec_md = f"**{title} ({count}건)**\n"
            if not issue_list:
                sec_md += "- 없음\n\n"
                return sec_md
                
            for item in issue_list:
                sec_md += f"- **[{item['summary']}]({item['link']})** `{item['badge_label']} : {item['date_str']}`\n"
                if item.get('is_hotfix'):
                    sec_md += f"  - 사유\n"
                    sec_md += f"    - 이슈원인 : {item.get('cause', '')}\n"
                    sec_md += f"    - 대응 : {item.get('action', '')}\n"
                    sec_md += f"    - TC존재여부 : {item.get('tc_exists', '')}\n"
            sec_md += "\n"
            return sec_md

        member_notion_md = ""
        if data['hotfixes']:
            member_notion_md += format_markdown_section(f"Hotfix ({len(data['hotfixes'])}건)", data['hotfixes'])
        member_notion_md += format_markdown_section("1. 완료", data['completed_last_week'])
        member_notion_md += format_markdown_section("2. 진행중", data['in_progress'])
        member_notion_md += format_markdown_section(f"3. 진행예정 (~{next_fri.strftime('%m/%d')})", data['planned_this_week'])
        member_notion_md += format_markdown_section("4. 이슈", data['bugs'])

        notion_full_text += f"👤 {member}\n" + member_notion_md

        def render_links(issue_list, is_hotfix_sec=False):
            if not issue_list:
                return "<div style='color:#94a3b8; font-size:13px;'>- 없음</div>"
            html = "<ul style='margin-left:20px; font-size:13px; line-height:1.8; color:#334155;'>"
            for item in issue_list:
                reason_detail_html = ""
                if is_hotfix_sec:
                    reason_detail_html = f"""
                    <div style='background:#fff5f5; border-left:3px solid #ef4444; padding:8px 12px; margin-top:6px; font-size:12px; border-radius:0 6px 6px 0; color:#475569;'>
                        <div style='font-weight:700; color:#b91c1c; margin-bottom:2px;'>사유</div>
                        <div>• 이슈원인 : {item.get('cause', '')}</div>
                        <div>• 대응 : {item.get('action', '')}</div>
                        <div>• TC존재여부 : {item.get('tc_exists', '')}</div>
                    </div>
                    """
                html += f"""<li style='margin-bottom:8px;'>
                    <a href='{item['link']}' target='_blank' style='color:#2563eb; font-weight:700; text-decoration:underline;'>{item['summary']}</a>
                    <span style='background:#f1f5f9; color:#475569; font-size:11px; padding:2px 6px; border-radius:4px; font-weight:600; margin-left:4px;'>{item['status']}</span>
                    <span style='background:#fee2e2; color:#dc2626; font-size:11px; padding:2px 6px; border-radius:4px; font-weight:700; margin-left:6px;'>{item['badge_label']} : {item['date_str']}</span>
                    {reason_detail_html}
                </li>"""
            html += "</ul>"
            return html

        hotfix_section_html = ""
        if data['hotfixes']:
            hotfix_section_html = f"""
            <div style="margin-bottom:14px; background:#fef2f2; border:1px solid #fca5a5; padding:12px; border-radius:8px;">
                <div style="font-size:14px; font-weight:700; color:#dc2626; margin-bottom:6px; display:flex; align-items:center; gap:6px;">
                    🔥 Hotfix ({len(data['hotfixes'])}건)
                </div>
                {render_links(data['hotfixes'], is_hotfix_sec=True)}
            </div>
            """

        member_cards_html += f"""
        <div style="background:white; border-radius:12px; border:1px solid #e2e8f0; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
            <div style="display:flex; justify-content:space-between; align-items:center; padding-bottom:10px; border-bottom:2px solid #f1f5f9; margin-bottom:14px;">
                <div style="font-size:18px; font-weight:700; color:#0f172a;">👤 {member}</div>
                <button class="member-copy-btn" id="btn_memberText_{idx}" onclick="copyTextToClipboard('memberText_{idx}', 'btn_memberText_{idx}')">📋 {member}님 목록 복사 (이름 제외)</button>
            </div>

            {hotfix_section_html}

            <div style="margin-bottom:14px;">
                <div style="font-size:14px; font-weight:700; color:#16a34a; margin-bottom:6px;">1. 완료 ({len(data['completed_last_week'])}건)</div>
                {render_links(data['completed_last_week'])}
            </div>

            <div style="margin-bottom:14px;">
                <div style="font-size:14px; font-weight:700; color:#dc2626; margin-bottom:6px;">2. 진행 중 ({len(data['in_progress'])}건)</div>
                {render_links(data['in_progress'])}
            </div>

            <div style="margin-bottom:14px;">
                <div style="font-size:14px; font-weight:700; color:#4f46e5; margin-bottom:6px;">3. 진행 예정 ({len(data['planned_this_week'])}건, ~{next_fri.strftime('%m/%d')} 금)</div>
                {render_links(data['planned_this_week'])}
            </div>

            <div>
                <div style="font-size:14px; font-weight:700; color:#d97706; margin-bottom:6px;">4. 이슈 ({len(data['bugs'])}건)</div>
                {render_links(data['bugs'])}
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
            .copy-btn {{ background: #4f46e5; color: white; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 700; font-size: 14px; cursor: pointer; transition: 0.2s; }}
            .copy-btn:hover {{ background: #4338ca; }}
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
    data, t_tue, t_nmon, t_mon, t_sun, n_fri = get_weekly_dashboard_data()
    html_out = build_weekly_html(data, t_tue, t_nmon, t_mon, t_sun, n_fri)
    
    os.makedirs("weekly", exist_ok=True)
    with open("weekly/index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
        
    print("🎉 성공! weekly/index.html 생성 완료!")

def get_weekly_html():
    data, t_tue, t_nmon, t_mon, t_sun, n_fri = get_weekly_dashboard_data()
    return build_weekly_html(data, t_tue, t_nmon, t_sun, n_fri)
