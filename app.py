import streamlit as st
import json
import os
import base64
import pandas as pd
import datetime
import threading
import time

# --- [1] 파일 경로 설정 및 상수 정의 ---
USERS_FILE = "users.json"
DATA_FILE = "learning_data.json"
CONFIG_FILE = "config.json"
UPLOAD_DIR = "uploads" 

os.makedirs(UPLOAD_DIR, exist_ok=True)

CLASS_GROUPS = ["1반", "2반", "3반", "4반"]
HUB_SCHOOLS = ["호계고등학교", "함월고등학교", "성광여자고등학교"]

# 💡 [요청 반영 6] 관리자 계정 단일화
ADMIN_ACCOUNTS = {
    "audskal": {"pw": "1847", "name": "김명남", "school": "신선여자고등학교"}
}

ACTIVITIES = [
    "[활동지1] 진학 희망 학과 조사하기",
    "[활동지2] 나만의 탐구 설계하기",
    "[활동지3] 주제 피드백에 따른 보완",
    "[활동지4] 참고 자료 조사",
    "[활동지5] 주제 탐구 보고서 양식",
    "[활동지6] 주제 탐구 보고서 피드백에 따른 보완",
    "[활동지7] 발표 피드백에 따른 보완",
    "[활동지8] 자기평가서",
    "[활동지9] 심화탐구 후속 활동 계획: 독서 연계 & 대입 로드맵"
]

INFO_BOX = "<div style='background-color: #f0f4f8; padding: 15px; border-radius: 8px; font-size: 17px; font-weight: 600; color: #222; margin-bottom: 15px; border-left: 5px solid #0056b3; line-height: 1.5;'>{}</div>"

# 💡 [요청 반영 2] 100명 동시 접속 대비 락 시스템
db_lock = threading.Lock()

# 💡 [요청 반영 3] 새로고침(F5) 시 로그아웃 방지 토큰 시스템
def encode_token(user_key, hub_school):
    raw = f"{user_key}|{hub_school}"
    return base64.b64encode(raw.encode('utf-8')).decode('utf-8')

def decode_token(token):
    try:
        decoded = base64.b64decode(token.encode('utf-8')).decode('utf-8')
        return decoded.split("|")
    except:
        return None, None

def load_json(file_path, default_value):
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(default_value, f, ensure_ascii=False, indent=4)
        return default_value
    for _ in range(5):
        try:
            with open(file_path, "r", encoding="utf-8") as f: 
                return json.load(f)
        except json.JSONDecodeError:
            time.sleep(0.1)
    return default_value

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def init_system():
    with db_lock:
        users = load_json(USERS_FILE, {})
        users_changed = False
        for adm_id, adm_info in ADMIN_ACCOUNTS.items():
            if adm_id not in users or users[adm_id].get("password") != adm_info["pw"] or users[adm_id].get("name") != adm_info["name"]:
                users[adm_id] = {
                    "id": adm_id, "password": adm_info["pw"], "name": adm_info["name"],
                    "role": "관리자", "school": adm_info["school"], "class_group": "관리자",
                    "approved": True, "hub_school": "전체" 
                }
                users_changed = True
        if users_changed: save_json(USERS_FILE, users)
        
        default_tabs = [f"{i}차시" for i in range(1, 9)]
        default_pdfs = {f"{i}차시": f"session{i}.pdf" for i in range(1, 9)}
        default_questions_template = [
            {"id": "q1", "label": "1. 오늘 배운 핵심 내용을 요약해보세요."},
            {"id": "q2", "label": "2. 이번 차시에서 가장 흥미로웠던 점은 무엇인가요?"},
            {"id": "q3", "label": "3. 질문이나 더 알아보고 싶은 점을 적어주세요."}
        ]
        default_questions = {tab: default_questions_template.copy() for tab in default_tabs}
        
        current_config = load_json(CONFIG_FILE, {})
        needs_update = False
        if current_config.get("tabs") != default_tabs:
            current_config["tabs"] = default_tabs
            current_config["pdfs"] = default_pdfs
            current_config["questions"] = default_questions
            needs_update = True
        else:
            for tab in default_tabs:
                if len(current_config.get("questions", {}).get(tab, [])) != 3:
                    if "questions" not in current_config: current_config["questions"] = {}
                    current_config["questions"][tab] = default_questions_template.copy()
                    needs_update = True
        if "materials" not in current_config:
            current_config["materials"] = []
            needs_update = True
        if needs_update: save_json(CONFIG_FILE, current_config)

def display_pdf(file_path):
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            base64_pdf = base64.b64encode(f.read()).decode('utf-8')
        st.markdown(f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="450" type="application/pdf"></iframe>', unsafe_allow_html=True)
    else: st.info(f"💡 교재 파일('{file_path}')이 폴더에 없습니다. 파일을 업로드하면 이곳에 표시됩니다.")

# --- [전체 / 개별 다운로드 통합 HTML 모듈] ---
def generate_html_report(u_info, student_answers, target_act=None, app_config=None):
    html_content = f"""<!DOCTYPE html>
    <html lang="ko">
    <head>
    <meta charset="UTF-8">
    <title>학습 포트폴리오</title>
    <style>
        body {{ font-family: 'Malgun Gothic', dotum, sans-serif; padding: 40px; line-height: 1.6; color: #333; }}
        h1 {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 20px; margin-bottom: 40px; }}
        h2 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 10px; margin-top: 40px; }}
        h3 {{ color: #2980b9; margin-top: 20px; }}
        h4 {{ color: #34495e; margin-top: 15px; border-bottom: 1px dashed #ccc; padding-bottom: 5px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 20px; font-size: 14px; }}
        th, td {{ border: 1px solid #bdc3c7; padding: 10px; text-align: left; }}
        th {{ background-color: #ecf0f1; font-weight: bold; text-align: center; }}
        .content-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #e9ecef; margin-bottom: 20px; white-space: pre-wrap; }}
        .link-text {{ color: #e74c3c; font-weight: bold; text-decoration: none; }}
    </style>
    </head>
    <body>
        <h1>📚 학습 포트폴리오</h1>
        <div style="text-align: right; margin-bottom: 30px; font-size: 16px;">
            <strong>학교:</strong> {u_info.get('school', '')}<br>
            <strong>소속:</strong> {u_info.get('class_group', '')}<br>
            <strong>이름:</strong> {u_info.get('name', '')} ({u_info.get('id', u_info.get('username', ''))})
        </div>
    """
    
    acts_to_render = [target_act] if target_act else ACTIVITIES
    if not target_act: html_content += "<h2>📑 [1] 활동지 작성 내역</h2>"
        
    for act in acts_to_render:
        ans = student_answers.get(act, {})
        if not ans and target_act is None: continue
        
        if act == ACTIVITIES[0] and ans.get("is_custom"):
            html_content += f"<h3>▶ {act}</h3><h4>[1단계] 추출한 핵심 내용 요소</h4><table><tr><th>학과/전공명</th><th>핵심 내용 요소</th></tr>"
            for row in ans.get("df1", []): html_content += f"<tr><td>{row.get('학과/전공명', '')}</td><td>{row.get('핵심 내용 요소', '')}</td></tr>"
            html_content += "</table><h4>[2단계] 생기부 탐구 내용 분석</h4><table><tr><th>구분</th><th>키워드1</th><th>키워드2</th><th>키워드3</th><th>키워드4</th><th>키워드5</th></tr>"
            for row in ans.get("df2", []): html_content += f"<tr><td><b>{row.get('구분', '')}</b></td><td>{row.get('키워드1','')}</td><td>{row.get('키워드2','')}</td><td>{row.get('키워드3','')}</td><td>{row.get('키워드4','')}</td><td>{row.get('키워드5','')}</td></tr>"
            html_content += f"</table><h4>[3단계] 탐구 주제 및 문제 인식</h4><div class='content-box'>{ans.get('step3', '')}</div>"
        elif act == ACTIVITIES[1] and ans.get("is_custom_act2"):
            html_content += f"<h3>▶ {act}</h3><h4>[1단계] 관심에서 출발하기</h4><p><b>관심 있는 것:</b> {ans.get('step1_1', '')}</p><p><b>계기:</b> {ans.get('step1_2', '')}</p><table><tr><th>구분</th><th>내용</th></tr>"
            for row in ans.get("df1", []): html_content += f"<tr><td>{row.get('구분', '')}</td><td>{row.get('내용', '')}</td></tr>"
            html_content += "</table><h4>[2단계] 교과 개념에 닻 내리기</h4><table><tr><th>구분</th><th>내용</th></tr>"
            for row in ans.get("df2", []): html_content += f"<tr><td>{row.get('구분', '')}</td><td>{row.get('내용', '')}</td></tr>"
            html_content += f"</table><p><b>잠정적 탐구 방향:</b> {ans.get('step2_dir', '')}</p><h4>[3단계] 주제 적절성 자가 진단</h4><table><tr><th>점검 항목</th><th>점검 질문</th><th>좋은 예</th><th>판단 (O/X)</th></tr>"
            for row in ans.get("df3", []): html_content += f"<tr><td>{row.get('점검 항목', '')}</td><td>{row.get('점검 질문', '')}</td><td>{row.get('좋은 예', '')}</td><td>{row.get('판단 (O/X)', '')}</td></tr>"
            html_content += "</table><h4>[4단계] 막연한 주제를 학술적 질문으로 바꾸기</h4>"
            html_content += f"<p><b>선택한 렌즈:</b> {ans.get('step4_1', '')}</p><p><b>핵심 탐구 질문:</b> {ans.get('step4_2', '')}</p><h4>[5단계] 탐구 전략 하나 정하기</h4>"
            html_content += f"<p><b>선택한 전략:</b> {ans.get('step5_1', '')}</p><p><b>구체적인 접근 방법:</b> {ans.get('step5_2', '')}</p><h4>[6단계] AI 멘토에게 점검받기</h4>"
            html_content += f"<div class='content-box'><b>반영할 점:</b><br>{ans.get('step6', '')}</div><h4>[7단계] 나의 탐구 계획 완성하기</h4><table><tr><th>항목</th><th>내용</th></tr>"
            for row in ans.get("df7", []): html_content += f"<tr><td>{row.get('항목', '')}</td><td>{row.get('내용', '')}</td></tr>"
            html_content += f"</table><p><b>요약:</b> 나는 <b>({ans.get('step7_s1', '')})</b> 개념을 활용해, <b>({ans.get('step7_s2', '')})</b> 전략으로 <b>({ans.get('step7_s3', '')})</b>을(를) 밝히려 한다.</p>"
        elif act in [ACTIVITIES[2], ACTIVITIES[5], ACTIVITIES[6]] and ans.get("is_custom_feedback"):
            html_content += f"<h3>▶ {act}</h3><table><tr><th>구분</th><th>피드백 내용 (구체적으로)</th><th>보완 및 수정 계획</th></tr>"
            for row in ans.get("df1", []): html_content += f"<tr><td><b>{row.get('구분','')}</b></td><td><pre>{row.get('피드백 내용 (구체적으로)','')}</pre></td><td><pre>{row.get('보완 및 수정 계획','')}</pre></td></tr>"
            html_content += "</table>"
        elif act == ACTIVITIES[3] and ans.get("is_custom_refs"):
            html_content += f"<h3>▶ {act}</h3><table><tr><th>사이트명</th><th>제목</th><th>내용</th><th>선정이유</th></tr>"
            for row in ans.get("df1", []): html_content += f"<tr><td>{row.get('사이트명','')}</td><td>{row.get('제목','')}</td><td>{row.get('내용','')}</td><td>{row.get('선정이유','')}</td></tr>"
            html_content += "</table>"
        elif act == ACTIVITIES[4] and ans.get("is_custom_act5"):
            html_content += f"<h3>▶ {act}</h3><h4>1. 기본 정보</h4><table>"
            html_content += f"<tr><th>교과명(강의명)</th><td>{ans.get('info_course', '')}</td><th>탐구 기간</th><td>{ans.get('info_date', '')}</td></tr>"
            html_content += f"<tr><th>소속학교</th><td>{ans.get('info_school', '')}</td><th>진로 희망</th><td>{ans.get('info_career', '')}</td></tr>"
            html_content += f"<tr><th>학번/이름</th><td>{ans.get('info_name', '')}</td><th>관련 교과, 단원</th><td>{ans.get('info_subject', '')}</td></tr>"
            html_content += f"<tr><th>탐구 방법</th><td>{ans.get('info_method', '')}</td><th>탐구 주제</th><td>{ans.get('info_topic', '')}</td></tr></table>"
            html_content += f"<h4>2. 탐구 개요</h4><p><b>가. 탐구 주제:</b> {ans.get('topic_title', '')}</p><p><b>나. 탐구 동기 및 배경</b></p>"
            html_content += f"<p>1) 교과 연계 동기: {ans.get('motive_1', '')}</p><p>2) 선정 배경: {ans.get('motive_2', '')}</p><p>3) 탐구 목적: {ans.get('purpose', '')}</p><p>4) 이론적 배경:</p><table><tr><th>구분</th><th>내용</th></tr>"
            for row in ans.get("bg_df", []): html_content += f"<tr><td>{row.get('구분', '')}</td><td>{row.get('내용', '')}</td></tr>"
            html_content += f"</table><h4>3. 탐구 설계 및 내용</h4><p><b>가. 탐구 방법:</b> {', '.join(ans.get('selected_methods', []))}</p><p><b>나. 세부 절차:</b></p><table><tr><th>순서</th><th>한 일</th></tr>"
            for row in ans.get("proc_df", []): html_content += f"<tr><td>{row.get('순서', '')}</td><td>{row.get('한 일', '')}</td></tr>"
            html_content += f"</table><p><b>다. 탐구 내용 (본론):</b></p><div class='content-box'>{ans.get('content_body', '')}</div><p><b>라. 탐구 결과</b></p>"
            html_content += f"<p>1) 결과 요약: {ans.get('result_summary', '')}</p><p>2) 해석 및 의의: {ans.get('result_meaning', '')}</p><h4>4. 결론 및 제언</h4>"
            html_content += f"<p><b>가. 결론:</b> {ans.get('conclusion', '')}</p><p><b>나. 성찰</b></p><p>1) 배우고 느낀 점: {ans.get('reflection_1', '')}</p><p>2) 한계점: {ans.get('reflection_2', '')}</p>"
            html_content += f"<p><b>다. 후속 활동:</b> {ans.get('next_step', '')}</p><h4>5. 참고 문헌</h4><p><b>가. 논문/도서:</b> {ans.get('ref_book', '')}</p><p><b>나. 웹사이트/기사:</b> {ans.get('ref_web', '')}</p>"
        elif act == ACTIVITIES[7] and ans.get("is_custom_self_eval"):
            html_content += f"<h3>▶ {act}</h3><table><tr><th>항목</th><th>내용</th></tr>"
            for row in ans.get("df1", []): html_content += f"<tr><td><b>{row.get('항목','')}</b></td><td><pre>{row.get('내용','')}</pre></td></tr>"
            html_content += "</table>"
        elif act == ACTIVITIES[8] and ans.get("is_custom_roadmap"):
            html_content += f"<h3>▶ {act}</h3><h4>1. 꼬리에 꼬리를 무는 독서</h4><table><tr><th>구분</th><th>도서명 / 저자</th><th>선정 이유 (탐구 활동과의 연결고리)</th></tr>"
            for row in ans.get("df1", []): html_content += f"<tr><td><b><pre>{row.get('구분','')}</pre></b></td><td>{row.get('도서명 / 저자','')}</td><td>{row.get('선정 이유 (탐구 활동과의 연결고리)','')}</td></tr>"
            html_content += "</table><h4>2. 나만의 3개년 실천 로드맵</h4><table><tr><th>시기</th><th>중점 목표</th><th>주요 활동 계획</th></tr>"
            for row in ans.get("df2", []): html_content += f"<tr><td><b><pre>{row.get('시기','')}</pre></b></td><td><pre>{row.get('중점 목표','')}</pre></td><td><pre>{row.get('주요 활동 계획 (주제탐구, 독서, 실험 등)','')}</pre></td></tr>"
            html_content += "</table>"
        else:
            ans_content = ans.get("content", {})
            if isinstance(ans_content, str): ans_content = {"text": ans_content}
            if ans_content.get("text") or ans_content.get("link"):
                html_content += f"<h3>▶ {act}</h3>"
                if ans_content.get("text"): html_content += f"<b>[텍스트 작성 내용]</b><div class='content-box'>{ans_content['text']}</div>"
                if ans_content.get("link"): html_content += f"<b>[제출 링크]</b> <a href='{ans_content['link']}' target='_blank' class='link-text'>{ans_content['link']}</a><br><br>"

    if not target_act and app_config:
        html_content += "<h2>📝 [2] 차시별 제출 자료</h2>"
        for t_name in app_config.get("tabs", []):
            for q in app_config.get("questions", {}).get(t_name, []):
                ans = student_answers.get(t_name, {}).get(q["id"], {})
                if isinstance(ans, str): ans = {"text": ans} 
                if ans.get("text"):
                    html_content += f"<h3>▶ [{t_name}] {q.get('label', '')}</h3>"
                    html_content += f"<div class='content-box'>{ans['text']}</div>"

    html_content += "</body></html>"
    return html_content

# 💡 [요청 반영 4] 학생 활동지 하단 개별 다운로드 버튼
def render_download_button(user_key, category):
    current_data = load_json(DATA_FILE, {})
    ans = current_data.get(user_key, {}).get(category, {})
    if ans:
        st.markdown("<br>", unsafe_allow_html=True)
        html_data = generate_html_report(st.session_state.user_info, current_data.get(user_key, {}), target_act=category)
        st.download_button(label=f"📥 제출한 내 내용 다운로드 ({category[:6]} AI 분석용)", 
                           data=html_data.encode('utf-8-sig'), 
                           file_name=f"{category}_내용.html", 
                           mime="text/html",
                           use_container_width=True)

# --- [3] 활동지별 맞춤형 폼 렌더링 함수들 ---
def render_activity1_form(user_key):
    category = ACTIVITIES[0]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.markdown(INFO_BOX.format("전공 가이드북을 활용하여 진학을 희망하는 학과의 핵심 내용 요소를 추출하고 이를 바탕으로 자신의 학교생활기록부의 탐구 활동을 분석/분류한다."), unsafe_allow_html=True)
    
    st.markdown("#### [1단계] 학과/전공 가이드북 읽고 핵심 내용 요소 추출하기")
    default_df1 = pd.DataFrame([{"학과/전공명": "", "핵심 내용 요소": ""} for _ in range(4)])
    df1 = pd.DataFrame(ans.get("df1", default_df1.to_dict('records')))
    edited_df1 = st.data_editor(df1, num_rows="dynamic", use_container_width=True, key="act1_df1")
    st.markdown("---")
    st.markdown("#### [2단계] 내용 요소 중심 학교생활기록부 탐구 내용 분석하기")
    st.info("💡 표의 가장 윗줄인 **'✏️ 나의 탐구 키워드'** 행의 빈칸을 더블클릭하여 본인의 키워드를 직접 입력하세요!")
    default_df2 = pd.DataFrame({
        "구분": ["✏️ 나의 탐구 키워드", "창체활동", "교과세특"],
        "키워드1": ["", "", ""], "키워드2": ["", "", ""], "키워드3": ["", "", ""], "키워드4": ["", "", ""], "키워드5": ["", "", ""]
    })
    df2 = pd.DataFrame(ans.get("df2", default_df2.to_dict('records')))
    edited_df2 = st.data_editor(df2, use_container_width=True, key="act1_df2")
    st.markdown("---")
    st.markdown("#### [3단계] 이번 특강을 통해 탐구하고 싶은 내용 영역 또는 문제 인식(주제 찾기)")
    st.markdown(INFO_BOX.format("내가 지금까지 다루지 못했던 내용 요소는 무엇이고 그것과 관련된 탐구 주제는 무엇이 있을까?"), unsafe_allow_html=True)
    step3_val = st.text_area("내용을 입력하세요", value=ans.get("step3", ""), height=150)

    if st.button("활동지 최종 제출 및 저장", key="submit_act1", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {"is_custom": True, "df1": edited_df1.to_dict('records'), "df2": edited_df2.to_dict('records'), "step3": step3_val}
            save_json(DATA_FILE, current_data)
        st.toast("🎉 활동지1이 성공적으로 저장되었습니다!")
        st.rerun()
    render_download_button(user_key, category)

def render_activity2_form(user_key):
    category = ACTIVITIES[1]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.markdown("### 교과서 속 지식을 세상의 해답으로 바꾸는 나만의 탐구 여정")
    st.markdown(INFO_BOX.format("이 활동지는 여러 주제를 나열하는 것이 아니라, <b>하나의 탐구 주제를 스스로 만들어 완성</b>하기 위한 것입니다. 1단계부터 순서대로 채워 나가면 마지막에 나만의 탐구 계획서가 완성됩니다. 각 단계는 앞 단계의 답을 이어받아 점점 구체화되도록 설계되어 있으니, 건너뛰지 말고 차례대로 작성해 보세요."), unsafe_allow_html=True)
    
    st.markdown("#### [1단계] 관심에서 출발하기")
    st.markdown(INFO_BOX.format("최근 궁금했던 것, 불편함을 느꼈던 것, 또는 수업이나 독서 중 더 알고 싶었던 것을 자유롭게 적어 봅니다."), unsafe_allow_html=True)
    step1_1 = st.text_input("(1) 내가 관심 있는 것 / 궁금했던 현상:", value=ans.get("step1_1", ""))
    opts1_2 = ["수업이나 책을 보다가 생긴 의문을 파고들고 싶어서 (지적호기심)", "배운 원리를 실생활 문제(사회 문제) 해결에 직접 적용해 보고 싶어서 (문제해결)", "이전에 했던 탐구에서 남은 궁금증이나 한계를 더 발전시키고 싶어서 (연계 및 심화)"]
    step1_2 = st.radio("(2) 이 관심이 생긴 계기 (하나만 선택)", opts1_2, index=opts1_2.index(ans.get("step1_2", opts1_2[0])) if ans.get("step1_2") in opts1_2 else 0)
    st.markdown("**이전에 했던 탐구의 확장을 선택했다면 아래도 함께 채우세요.**")
    st.caption("※ 첫 번째 열(구분)은 양식 제목이므로 수정하지 마세요.")
    default_df1 = pd.DataFrame([{"구분": "이전에 했던 탐구 주제", "내용": ""}, {"구분": "그때 알아낸 것 (결론)", "내용": ""}, {"구분": "풀지 못했거나 아쉬웠던 점", "내용": ""}, {"구분": "이번에 발전시키고 싶은 방향", "내용": "(예: 대상 확대, 조건 변경, 다른 관점 적용, 해결책 제시 등)"}])
    df1 = pd.DataFrame(ans.get("df1", default_df1.to_dict('records')))
    edited_df1 = st.data_editor(df1, hide_index=True, use_container_width=True, disabled=["구분"], key="act2_df1")
    st.markdown("---")
    
    st.markdown("#### [2단계] 교과 개념에 닻 내리기: 내 관심사를 수업에서 배운 원리와 연결하기")
    st.markdown(INFO_BOX.format("좋은 탐구는 단순한 검색이 아니라 배운 개념 위에서 출발합니다. 과목 이름만 적지 말고, 배운 핵심 개념 하나를 구체적으로 적는 것이 중요합니다."), unsafe_allow_html=True)
    st.caption("※ 첫 번째 열(구분)은 양식 제목이므로 수정하지 마세요.")
    default_df2 = pd.DataFrame([{"구분": "관련 교과목, 단원", "내용": "(예: 확률과 통계 - 통계적 추정)"}, {"구분": "수업에서 배운 핵심 개념", "내용": "(예: 표본 조사에서의 신뢰구간과 오차)"}, {"구분": "이 개념이 내 관심사와 연결되는 지점", "내용": ""}])
    df2 = pd.DataFrame(ans.get("df2", default_df2.to_dict('records')))
    edited_df2 = st.data_editor(df2, hide_index=True, use_container_width=True, disabled=["구분"], key="act2_df2")
    step2_dir = st.text_input("**이 시점의 잠정적인 탐구 방향 (한 문장으로):**", value=ans.get("step2_dir", ""))
    st.markdown("---")
    
    st.markdown("#### [3단계] 주제 적절성 자가 진단: 주제가 고등학생 수준에 맞는지 점검")
    st.caption("※ '판단(O/X)' 열에만 답변을 기입하세요.")
    default_df3 = pd.DataFrame([
        {"점검 항목": "1. 교과 개념 연계", "점검 질문": "교과서의 원리 없이 인터넷 검색만으로 알 수 있는 상식적인 내용인가?", "좋은 예": "(나쁜 예) 비타민을 많이 먹으면 좋은지 조사 → (좋은 예) 생명과학 I '방어 작용'을 바탕으로 면역 결핍과 자가면역이 일어나는 기작의 차이 비교", "판단 (O/X)": ""},
        {"점검 항목": "2. 시의성 (트렌드)", "점검 질문": "너무 유행을 타거나(정치적 이슈 등), 이미 결론이 난 철 지난 주제는 아닌가?", "좋은 예": "(나쁜 예) 특정 정치인을 화자로 시 쓰기 → (좋은 예) 현대 시 한 편을 문학 사조별 대표 시인의 화법으로 다시 쓰기...", "판단 (O/X)": ""},
        {"점검 항목": "3. 수행 가능성", "점검 질문": "고등학생의 시간·장비·비용으로 실제 조사나 실험이 가능한가?", "좋은 예": "(나쁜 예) 층간소음을 완벽히 없애는 신소재 발명 → (좋은 예) 물-시멘트 배합 비율 및 첨가제 유무에 따른 콘크리트 압축 강도 변화 측정", "판단 (O/X)": ""},
        {"점검 항목": "4. 동기의 진정성", "점검 질문": "억지로 끼워 맞춘 것이 아니라 1단계의 계기에서 자연스럽게 이어지는가?", "좋은 예": "1단계에 적은 관심·계기와 흐름이 연결되는지 확인", "판단 (O/X)": ""}
    ])
    df3 = pd.DataFrame(ans.get("df3", default_df3.to_dict('records')))
    edited_df3 = st.data_editor(df3, hide_index=True, use_container_width=True, disabled=["점검 항목", "점검 질문", "좋은 예"], key="act2_df3")
    st.info("자가 진단 결과: 모든 항목이 O라면 다음 단계로 넘어가세요. 하나라도 X라면 주제를 수정해야 합니다.")
    st.markdown("---")
    
    st.markdown("#### [4단계] 막연한 주제를 학술적 질문으로 바꾸기")
    st.markdown(INFO_BOX.format("좋은 탐구는 좋은 질문에서 시작합니다. 아래 여섯 개의 질문 렌즈 중 딱 하나를 골라, 내 주제를 탐구 가능한 질문으로 바꿔 봅니다."), unsafe_allow_html=True)
    opts4_1 = ["1. 원인과 결과", "2. 비교와 대조", "3. 평가와 가치", "4. 분류와 특징", "5. 변화 과정", "6. 기타"]
    step4_1 = st.selectbox("1) 선택한 렌즈:", opts4_1, index=opts4_1.index(ans.get("step4_1", opts4_1[0])) if ans.get("step4_1") in opts4_1 else 0)
    step4_2 = st.text_input("2) 완성한 나의 핵심 탐구 질문:", value=ans.get("step4_2", ""))
    st.markdown("---")
    
    st.markdown("#### [5단계] 탐구 전략 하나 정하기")
    opts5_1 = ["현미경 (원리 분석)", "벤치마킹 (비교 탐구)", "색안경 (관점 적용)", "의사 (문제 해결)"]
    step5_1 = st.selectbox("2) 내가 선택한 전략은?", opts5_1, index=opts5_1.index(ans.get("step5_1", opts5_1[0])) if ans.get("step5_1") in opts5_1 else 0)
    step5_2 = st.text_area("3) 이 전략으로 질문에 접근하는 구체적인 방법 (2~3문장)", value=ans.get("step5_2", ""))
    st.markdown("---")
    
    st.markdown("#### [6단계] AI 멘토에게 점검받기")
    st.markdown(INFO_BOX.format("내가 세운 전략이 논리적이고 고등학생 수준에서 수행 가능한지 AI에게 물어보고 부족한 점을 보완하세요."), unsafe_allow_html=True)
    
    step6 = st.text_area("2) AI의 조언 중 내가 반영할 점:", value=ans.get("step6", ""))
    st.markdown("---")
    
    st.markdown("#### [7단계] 나의 탐구 계획 완성하기")
    st.caption("※ 첫 번째 열(항목)은 양식 제목이므로 수정하지 마세요.")
    default_df7 = pd.DataFrame([
        {"항목": "탐구 주제 (제목)", "내용": ""}, {"항목": "핵심 탐구 질문", "내용": ""}, {"항목": "연계 교과 개념", "내용": ""},
        {"항목": "탐구 전략", "내용": ""}, {"항목": "탐구 방법 (자료, 조사, 실험 등)", "내용": ""}, {"항목": "기대하는 결론, 알아낼 점", "내용": ""}
    ])
    df7 = pd.DataFrame(ans.get("df7", default_df7.to_dict('records')))
    edited_df7 = st.data_editor(df7, hide_index=True, use_container_width=True, disabled=["항목"], key="act2_df7")
    
    st.markdown("<br>**한 문장으로 요약한 나의 탐구**", unsafe_allow_html=True)
    col1, col2, col3, col4, col5, col6, col7 = st.columns([0.5, 2, 1.2, 2, 0.8, 2, 1.5])
    with col1: st.write("나는 (")
    with col2: step7_s1 = st.text_input("개념", value=ans.get("step7_s1", ""), label_visibility="collapsed")
    with col3: st.write(") 개념을 활용해, (")
    with col4: step7_s2 = st.text_input("전략", value=ans.get("step7_s2", ""), label_visibility="collapsed")
    with col5: st.write(") 전략으로 (")
    with col6: step7_s3 = st.text_input("밝힐점", value=ans.get("step7_s3", ""), label_visibility="collapsed")
    with col7: st.write(")을(를) 밝히려 한다.")

    if st.button("활동지 최종 제출 및 저장", key="submit_act2", type="primary"):
        with db_lock: 
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {
                "is_custom_act2": True, "step1_1": step1_1, "step1_2": step1_2,
                "df1": edited_df1.to_dict('records'), "df2": edited_df2.to_dict('records'),
                "step2_dir": step2_dir, "df3": edited_df3.to_dict('records'),
                "step4_1": step4_1, "step4_2": step4_2, "step5_1": step5_1, "step5_2": step5_2,
                "step6": step6, "df7": edited_df7.to_dict('records'),
                "step7_s1": step7_s1, "step7_s2": step7_s2, "step7_s3": step7_s3
            }
            save_json(DATA_FILE, current_data)
        st.toast("🎉 활동지2가 성공적으로 저장되었습니다!")
        st.rerun()
    render_download_button(user_key, category)

def render_feedback_form(user_key, category, rows):
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.caption("※ 첫 번째 열(구분)은 양식 제목이므로 수정하지 마세요.")
    default_df = pd.DataFrame([{"구분": r, "피드백 내용 (구체적으로)": "", "보완 및 수정 계획": ""} for r in rows])
    df = pd.DataFrame(ans.get("df1", default_df.to_dict('records')))
    edited_df = st.data_editor(df, hide_index=True, use_container_width=True, disabled=["구분"], key=f"df_{category}")
    if st.button("활동지 최종 제출 및 저장", key=f"btn_{category}", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {})
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {"is_custom_feedback": True, "df1": edited_df.to_dict('records')}
            save_json(DATA_FILE, current_data)
        st.toast(f"🎉 {category} 저장 완료!")
        st.rerun()
    render_download_button(user_key, category)

def render_activity3_form(user_key): render_feedback_form(user_key, ACTIVITIES[2], ["참고 아이디어", "방향 제안", "함께 찾아보면 좋을 교과 키워드"])
def render_activity6_form(user_key): render_feedback_form(user_key, ACTIVITIES[5], ["주제 선정과 교과 연결", "질문 만들기와 탐구 방법", "자료 찾기 및 내용 소화", "나만의 생각 더하기", "마무리 및 다음 단계"])
def render_activity7_form(user_key): render_feedback_form(user_key, ACTIVITIES[6], ["논리 및 근거\n(내용의 타당성)", "AI 활용 적절성\n(도구 활용 능력)", "발표 및 전달력\n(의사소통 능력)"])

def render_activity4_form(user_key):
    category = ACTIVITIES[3]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.markdown("<i>탐구 과정 중 참고하게 되는 자료 목록을 여기에 지속적으로 추가하고, 노트북LM의 소스로 활용합니다.</i>", unsafe_allow_html=True)
    default_df = pd.DataFrame([{"사이트명": "", "제목": "", "내용": "", "선정이유": ""} for _ in range(5)])
    df = pd.DataFrame(ans.get("df1", default_df.to_dict('records')))
    edited_df = st.data_editor(df, num_rows="dynamic", hide_index=True, use_container_width=True, key=f"df_{category}")
    if st.button("활동지 최종 제출 및 저장", key=f"btn_{category}", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {})
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {"is_custom_refs": True, "df1": edited_df.to_dict('records')}
            save_json(DATA_FILE, current_data)
        st.toast("🎉 저장 완료!")
        st.rerun()
    render_download_button(user_key, category)

def render_activity5_form(user_key):
    category = ACTIVITIES[4]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    
    st.markdown("#### 1. 기본 정보")
    c1, c2, c3, c4 = st.columns([1, 2, 1, 2])
    c1.markdown("**교과명(강의명)**"); info_course = c2.text_input("교과명", value=ans.get("info_course", "탐구력 신장을 위한 주제 탐구 캠프"), label_visibility="collapsed")
    c3.markdown("**탐구 기간**"); info_date = c4.text_input("탐구 기간", value=ans.get("info_date", "2026. 7. 23. ~ 7. 24."), label_visibility="collapsed")
    c1, c2, c3, c4 = st.columns([1, 2, 1, 2])
    c1.markdown("**소속학교**"); info_school = c2.text_input("소속학교", value=ans.get("info_school", st.session_state.user_info.get("school", "")), label_visibility="collapsed")
    c3.markdown("**진로 희망**"); info_career = c4.text_input("진로 희망", value=ans.get("info_career", ""), label_visibility="collapsed")
    c1, c2, c3, c4 = st.columns([1, 2, 1, 2])
    c1.markdown("**학번/이름**"); default_id_name = f"{st.session_state.user_info.get('username', '')} {st.session_state.user_info.get('name', '')}"
    info_name = c2.text_input("학번/이름", value=ans.get("info_name", default_id_name), label_visibility="collapsed")
    c3.markdown("**관련 교과, 단원**"); info_subject = c4.text_input("관련 교과", value=ans.get("info_subject", ""), placeholder="(활동지2 2단계 참고)", label_visibility="collapsed")
    c1, c2, c3, c4 = st.columns([1, 2, 1, 2])
    c1.markdown("**탐구 방법**"); info_method = c2.radio("탐구 방법", ["교과 심화", "진로 심화"], index=0 if ans.get("info_method", "교과 심화") == "교과 심화" else 1, horizontal=True, label_visibility="collapsed")
    c3.markdown("**탐구 주제**"); info_topic = c4.text_input("탐구 주제", value=ans.get("info_topic", ""), placeholder="(활동지2 7단계 참고)", label_visibility="collapsed")

    st.markdown("---")
    st.markdown("#### 2. 탐구 개요\n**가. 탐구 주제**")
    topic_title = st.text_area("탐구 주제", value=ans.get("topic_title", ""), placeholder="구체적이고 명확한 주제명을 입력하세요. (예: 학교 급식 잔반량 설문 조사를 통해 원인을 분석하고 줄일 방법을 찾는다.)", label_visibility="collapsed")
    st.markdown("**나. 탐구 동기 및 배경\n1) 교과 연계 동기**")
    motive_1 = st.text_area("교과 연계 동기", value=ans.get("motive_1", ""), placeholder="어떤 과목, 어떤 단원 수업 중 의문이 생겼나요? 구체적으로 서술하세요.", label_visibility="collapsed")
    st.markdown("**2) 선정 배경**")
    motive_2 = st.text_area("선정 배경", value=ans.get("motive_2", ""), placeholder="왜 이 주제를 골랐나요? 평소 흥미나 사회 문제와 연결해 보세요.", label_visibility="collapsed")
    st.markdown("**3) 탐구 목적**")
    purpose = st.text_area("탐구 목적", value=ans.get("purpose", ""), placeholder="이 탐구로 최종적으로 무엇을 알아내거나 해결하려 하나요?", label_visibility="collapsed")
    st.markdown("**4) 이론적 배경**")
    
    default_bg_df = pd.DataFrame([{"구분": "핵심 용어의 뜻", "내용": "(활동지2의 2단계 교과 개념을 사전과 교과서 정의로 정리)"}, {"구분": "참고한 자료 요약", "내용": "(관련 책, 기사, 논문에서 알게 된 내용을 2~3줄로 정리)"}])
    bg_df = pd.DataFrame(ans.get("bg_df", default_bg_df.to_dict('records')))
    edited_bg_df = st.data_editor(bg_df, hide_index=True, use_container_width=True, num_rows="dynamic", key="act5_bg_df")

    st.markdown("---")
    st.markdown("#### 3. 탐구 설계 및 내용\n**가. 탐구 방법**")
    methods = ["문헌연구(비교분석)", "데이터분석", "설문/인터뷰", "실험", "기타"]
    selected_methods = st.multiselect("탐구 방법 선택", methods, default=ans.get("selected_methods", []))
    st.markdown("**나. 세부 절차**")
    default_proc_df = pd.DataFrame([{"순서": "1", "한 일": "(예: 조사 대상과 범위 정하기)"}, {"순서": "2", "한 일": "(예: 자료 수집, 설문 실시, 실험 진행)"}, {"순서": "3", "한 일": "(예: 결과 정리 및 표나 그래프로 나타내기)"}, {"순서": "4", "한 일": "(예: 결과 해석하기)"}])
    proc_df = pd.DataFrame(ans.get("proc_df", default_proc_df.to_dict('records')))
    edited_proc_df = st.data_editor(proc_df, hide_index=True, use_container_width=True, num_rows="dynamic", key="act5_proc_df")
    st.markdown("**다. 탐구 내용 (본론)**")
    content_body = st.text_area("탐구 내용 (본론)", value=ans.get("content_body", ""), placeholder="수집한 자료나 데이터를 바탕으로 실제로 알아낸 내용을 씁니다.", height=200, label_visibility="collapsed")
    st.markdown("**라. 탐구 결과\n1) 결과 요약**")
    result_summary = st.text_area("결과 요약", value=ans.get("result_summary", ""), placeholder="알아낸 사실이나 데이터 중 핵심만 간단히", label_visibility="collapsed")
    st.markdown("**2) 해석 및 의의**")
    result_meaning = st.text_area("해석 및 의의", value=ans.get("result_meaning", ""), placeholder="그 결과가 무엇을 뜻하는지, 예상과 어떻게 달랐는지 나의 생각을 쓰세요.", label_visibility="collapsed")

    st.markdown("---")
    st.markdown("#### 4. 결론 및 제언\n**가. 결론**")
    conclusion = st.text_area("결론", value=ans.get("conclusion", ""), placeholder="탐구 목적을 달성했는지 확인하고, 최종 답을 한두 문장으로 정리하세요.", label_visibility="collapsed")
    st.markdown("**나. 성찰\n1) 배우고 느낀 점**")
    reflection_1 = st.text_area("배우고 느낀 점", value=ans.get("reflection_1", ""), placeholder="새로 알게 된 지식, 깨달음, 내가 기울인 노력", label_visibility="collapsed")
    st.markdown("**2) 한계점**")
    reflection_2 = st.text_area("한계점", value=ans.get("reflection_2", ""), placeholder="아쉬웠던 점과 부족했던 부분", label_visibility="collapsed")
    st.markdown("**다. 후속 활동**")
    next_step = st.text_area("후속 활동", value=ans.get("next_step", ""), placeholder="이 탐구를 발전시켜 더 알아보고 싶은 것, 읽어 볼 책을 적으세요.", label_visibility="collapsed")

    st.markdown("---")
    st.markdown("#### 5. 참고 문헌\n**가. 논문/도서**")
    ref_book = st.text_area("논문/도서", value=ans.get("ref_book", ""), placeholder="저자, 제목, 출판사(발행처), 발행연도, 페이지", label_visibility="collapsed")
    st.markdown("**나. 웹사이트/기사**")
    ref_web = st.text_area("웹사이트/기사", value=ans.get("ref_web", ""), placeholder="사이트명, 기사 제목, URL, 접속일자", label_visibility="collapsed")

    if st.button("활동지 최종 제출 및 저장", key="btn_act5", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {})
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {
                "is_custom_act5": True, "info_course": info_course, "info_date": info_date, "info_school": info_school, "info_career": info_career,
                "info_name": info_name, "info_subject": info_subject, "info_method": info_method, "info_topic": info_topic,
                "topic_title": topic_title, "motive_1": motive_1, "motive_2": motive_2, "purpose": purpose,
                "bg_df": edited_bg_df.to_dict('records'), "selected_methods": selected_methods, "proc_df": edited_proc_df.to_dict('records'),
                "content_body": content_body, "result_summary": result_summary, "result_meaning": result_meaning,
                "conclusion": conclusion, "reflection_1": reflection_1, "reflection_2": reflection_2,
                "next_step": next_step, "ref_book": ref_book, "ref_web": ref_web
            }
            save_json(DATA_FILE, current_data)
        st.toast("🎉 저장되었습니다!")
        st.rerun()
    render_download_button(user_key, category)

def render_activity8_form(user_key):
    category = ACTIVITIES[7]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.caption("※ 첫 번째 열(항목)은 양식 제목이므로 수정하지 마세요.")
    default_df = pd.DataFrame([
        {"항목": "1. 탐구 주제", "내용": ""}, {"항목": "2. [의문] 주제 선정 동기", "내용": ""},
        {"항목": "3. [문제해결] 과정 및 결과 요약", "내용": ""}, {"항목": "4. 활동에서 자신의 역할과 노력", "내용": ""},
        {"항목": "5. 배우고 느낀 점", "내용": ""}, {"항목": "6. 새롭게 알게 된 것", "내용": ""}, {"항목": "7. 후속 활동 계획", "내용": ""}
    ])
    df = pd.DataFrame(ans.get("df1", default_df.to_dict('records')))
    edited_df = st.data_editor(df, hide_index=True, use_container_width=True, disabled=["항목"], key=f"df_{category}")
    if st.button("활동지 최종 제출 및 저장", key="btn_act8", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {})
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {"is_custom_self_eval": True, "df1": edited_df.to_dict('records')}
            save_json(DATA_FILE, current_data)
        st.toast("🎉 저장되었습니다!")
        st.rerun()
    render_download_button(user_key, category)

def render_activity9_form(user_key):
    category = ACTIVITIES[8]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    
    st.markdown("#### 1. 꼬리에 꼬리를 무는 독서")
    st.caption("※ 첫 번째 열(구분)은 양식 제목이므로 수정하지 마세요.")
    default_df1 = pd.DataFrame([
        {"구분": "기초 도서\n(흥미/입문)", "도서명 / 저자": "", "선정 이유 (탐구 활동과의 연결고리)": "이 책의 ____________ 부분을 읽고 ____________ 개념에 흥미 가짐."},
        {"구분": "심화 도서\n(전공/이론)", "도서명 / 저자": "", "선정 이유 (탐구 활동과의 연결고리)": "위 책에서 생긴 ____________ 에 대한 궁금증을 해결하기 위해 선정함."},
        {"구분": "확장 도서\n(융합/원서)", "도서명 / 저자": "", "선정 이유 (탐구 활동과의 연결고리)": "관련 해외 원서나 논문을 읽고 ____________ 관점까지 확장함."}
    ])
    df1 = pd.DataFrame(ans.get("df1", default_df1.to_dict('records')))
    edited_df1 = st.data_editor(df1, hide_index=True, use_container_width=True, disabled=["구분"], key="act9_df1")

    st.markdown("#### 2. 나만의 3개년 실천 로드맵")
    st.caption("※ 첫 번째와 두 번째 열은 양식 제목이므로 수정하지 마세요.")
    default_df2 = pd.DataFrame([
        {"시기": "1학년\n(겨울방학)", "중점 목표": "기초 역량\n& 진로 탐색", "주요 활동 계획 (주제탐구, 독서, 실험 등)": "□ \n□ \n□ \n□ "},
        {"시기": "2학년\n(1학기)", "중점 목표": "구체적 주제\n심화 탐구", "주요 활동 계획 (주제탐구, 독서, 실험 등)": "□ \n□ \n□ \n□ "},
        {"시기": "2학년\n(2학기/겨울)", "중점 목표": "전공 적합성\n증명 & 융합", "주요 활동 계획 (주제탐구, 독서, 실험 등)": "□ \n□ \n□ \n□ "},
        {"시기": "3학년\n(1학기)", "중점 목표": "완성\n& 면접 대비", "주요 활동 계획 (주제탐구, 독서, 실험 등)": "□ \n□ \n□ \n□ "}
    ])
    df2 = pd.DataFrame(ans.get("df2", default_df2.to_dict('records')))
    edited_df2 = st.data_editor(df2, hide_index=True, use_container_width=True, disabled=["시기", "중점 목표"], key="act9_df2")

    if st.button("활동지 최종 제출 및 저장", key="btn_act9", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {})
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {"is_custom_roadmap": True, "df1": edited_df1.to_dict('records'), "df2": edited_df2.to_dict('records')}
            save_json(DATA_FILE, current_data)
        st.toast("🎉 저장되었습니다!")
        st.rerun()
    render_download_button(user_key, category)

# --- 캠프 종합 공지 렌더링 ---
def render_camp_overview(current_role, current_hub):
    st.header(f"🎯 [거점: {current_hub}] 주제 탐구 캠프 (26-하계방학)")
    st.markdown("---")
    st.subheader("🗓️ 7/23(목) ~ 7/24(금) 일정")
    schedule_data = [
        ["1일차", "1 (09:00~10:40)", "학생부 종합 전형과 탐구 역량의 이해", "우수 기록 사례 분석 및 인공지능 탐색 툴 활용법 익히기"],
        ["1일차", "2 (11:00~12:40)", "학과 안내서(가이드북)를 활용한 나의 학생부 분석", "학과별 요구 역량 파악 및 개인별 학생부 강점·보완점 분석 활동"],
        ["1일차", "점심 (12:40~13:30)", "점심식사", "점심식사"],
        ["1일차", "3 (13:30~15:10)", "탐구 주제 선정 및 보고서 개요(목차) 작성법", "인공지능을 활용한 개인별 맞춤형 탐구 주제 및 목차 설계"],
        ["1일차", "4 (15:30~17:10)", "탐구 보고서 설계도 작성 및 자료 수집", "논문, 해외 원문, 도서, 기사 등 다각적 자료 수집 및 개요 구체화"],
        ["2일차", "1 (09:00~10:40)", "탐구 보고서 본문 작성 및 내용 보완", "수집한 자료를 바탕으로 보고서 초안 작성 및 자체 점검"],
        ["2일차", "2 (11:00~12:40)", "탐구 결과 요약 및 시각화 자료 구성", "보고서 핵심 내용 요약 및 인포그래픽, 시각 자료 제작 활동"],
        ["2일차", "점심 (12:40~13:30)", "점심식사", "점심식사"],
        ["2일차", "3 (13:30~15:10)", "탐구 보고서 최종 수정 및 고도화", "작성된 보고서의 논리 구조 점검 및 문장 다듬기 완성 활동"],
        ["2일차", "4 (15:30~17:10)", "후속 탐구 활동 계획 및 학년별 실천 로드맵 수립", "연계 독서 활동 계획 수립 및 교과 학습 실천 로드맵 정리"]
    ]
    st.dataframe(pd.DataFrame(schedule_data, columns=["일자", "차시(시간)", "수업내용", "활동내용"]), use_container_width=True, hide_index=True)
    st.markdown("---")
    
    app_config = load_json(CONFIG_FILE, {})
    materials = app_config.get("materials", [])
    if materials:
        st.subheader("👨‍🏫 캠프 특강 및 강의 자료실")
        for mat in materials:
            if mat["type"] == "link": st.markdown(f"🔗 **[{mat['title']}]({mat['content']})**")
            elif mat["type"] == "file":
                if os.path.exists(mat["content"]):
                    if current_role in ["관리자", "교사"]:
                        with open(mat["content"], "rb") as f: st.download_button(f"📥 {mat['title']} ({mat['filename']}) 다운로드", f, file_name=mat['filename'], key=f"mat_dl_{mat['id']}")
                    else: st.markdown(f"🔒 **{mat['title']}** (학생 다운로드 제한 자료)")
        st.markdown("---")

    link_style = "font-size: 18px; font-weight: bold; color: #0056b3; text-decoration: none; display: block; margin-bottom: 10px;"
    col1, col2 = st.columns(2)
    with col1:
        with st.expander("👥 캠프 사전 안내", expanded=True):
            st.markdown(f"<a href='https://app.notion.com/p/26-3a1b5d2009278095b09cd44692be6056?pvs=11' target='_blank' style='{link_style}'>🔗 캠프 사전 안내 노션 사이트</a>", unsafe_allow_html=True)
            st.markdown(f"<a href='https://forms.gle/4Co5GLdD3M6KEVcs8' target='_blank' style='{link_style}'>🔗 사전 설문조사 [구글 폼]</a>", unsafe_allow_html=True)
        with st.expander("📝 활동지 링크 (클릭 시 이동 및 작성)", expanded=True):
            st.caption("아래 버튼을 누르면 프로그램 내 제출 화면으로 전환됩니다.")
            for act in ACTIVITIES:
                if st.button(f"📄 {act}", use_container_width=True):
                    st.session_state.current_page = act; st.rerun()
    with col2:
        with st.expander("📚 대학 전공 가이드북 링크", expanded=True):
            st.markdown(f"<a href='https://drive.google.com/drive/folders/18TOhHc0kVvQBa5UcbwlvkQkglOYax8xZ?usp=sharing' target='_blank' style='{link_style}'>📁 대학 전공 가이드북 구글 드라이브 폴더 열기</a>", unsafe_allow_html=True)
        with st.expander("📊 만족도 조사 설문 링크 (QR 포함)", expanded=True):
            st.markdown(f"<a href='https://forms.gle/kqjWnsTE65Jf8QCS6' target='_blank' style='{link_style}'>🔗 캠프 만족도 조사 참여하기 (Google Forms)</a>", unsafe_allow_html=True)

# --- [4] 메인 프로그램 세팅 및 사이드바 ---
st.set_page_config(page_title="주제 탐구 캠프 시스템", layout="wide")

st.markdown("""
<style>
[data-testid="stFormSubmitButton"] button, button[kind="primary"] {
    background-color: #FF4B4B !important;
    color: white !important;
    font-size: 24px !important;
    font-weight: 900 !important;
    padding: 15px !important;
    border-radius: 8px !important;
    border: none !important;
    min-height: 60px !important;
    width: 100% !important;
}
[data-testid="stFormSubmitButton"] button p, button[kind="primary"] p {
    font-size: 24px !important;
    font-weight: 900 !important;
    color: white !important;
}
div.element-container:has(.back-btn-wrapper) + div.element-container button {
    background-color: #0056b3 !important;
    color: white !important;
    font-size: 24px !important;
    font-weight: 900 !important;
    padding: 15px !important;
    border-radius: 8px !important;
    border: none !important;
    min-height: 60px !important;
}
div.element-container:has(.back-btn-wrapper) + div.element-container button p {
    font-size: 24px !important;
    font-weight: 900 !important;
    color: white !important;
}
.back-btn-wrapper { display: none; }
.stMarkdown p {
    font-size: 17px !important;
    color: #222222 !important;
    font-weight: 600 !important;
    line-height: 1.6 !important;
}
.stTextArea textarea {
    font-size: 17px !important;
    color: #000000 !important;
    border: 2px solid #aaa !important;
    font-weight: 500 !important;
}
button[kind="secondary"] p {
    color: #000000 !important;
    font-weight: 900 !important;
    font-size: 16px !important;
}
[data-testid="stDataFrame"] {
    border: 2px solid #333 !important;
    border-radius: 5px;
}
[data-testid="stDataFrame"] * {
    color: #000000 !important;
}
[data-testid="stDataFrame"] span {
    font-size: 16px !important;
    font-weight: 900 !important;
    color: #000000 !important;
}
table th {
    background-color: #f0f2f6 !important;
    color: #000000 !important;
    font-size: 18px !important;
    font-weight: 900 !important;
}
table td {
    color: #000000 !important;
    font-size: 16px !important;
    font-weight: 600 !important;
}
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stSelectbox label p,
[data-testid="stSidebar"] .stTextInput label p,
[data-testid="stSidebar"] .stRadio label p,
[data-testid="stSidebar"] div[data-baseweb="radio"] div {
    font-size: 17px !important;
    font-weight: 700 !important;
    color: #111111 !important;
}
[data-testid="stSidebar"] input {
    font-size: 16px !important;
    font-weight: 500 !important;
}
</style>
""", unsafe_allow_html=True)

init_system()

if "logged_in" not in st.session_state: 
    st.session_state.logged_in = False
    st.session_state.user_info = None

if not st.session_state.logged_in and "session_token" in st.query_params:
    token = st.query_params["session_token"]
    user_key, login_hub = decode_token(token)
    
    if user_key:
        users = load_json(USERS_FILE, {})
        if user_key in ADMIN_ACCOUNTS:
            admin_info = ADMIN_ACCOUNTS[user_key]
            st.session_state.logged_in = True
            st.session_state.user_info = {"user_key": user_key, "username": user_key, "name": admin_info["name"], "role": "관리자", "school": admin_info["school"], "class_group": "관리자", "hub_school": login_hub}
        elif user_key in users and users[user_key].get("approved", False):
            db_role = users[user_key].get("role")
            st.session_state.logged_in = True
            st.session_state.user_info = {"user_key": user_key, "username": users[user_key].get("id", ""), "name": users[user_key].get("name", "이름없음"), "role": db_role, "school": users[user_key].get("school", "소속없음"), "class_group": users[user_key].get("class_group", "미배정"), "hub_school": login_hub}

if "current_page" not in st.session_state: st.session_state.current_page = "main"

st.sidebar.title("🔒 인증 센터")
if st.session_state.logged_in:
    u_info = st.session_state.user_info
    
    u_name = u_info['name']
    u_school = u_info.get('school', '소속없음')
    u_class = u_info.get('class_group', '')
    u_role = u_info['role']
    u_hub = u_info.get('hub_school', '호계고등학교')
    school_text = f"{u_school} ({u_class})" if u_class and u_class != "관리자" else u_school
    
    st.sidebar.markdown(f"""
    <div style='background-color: #e8f4f8; padding: 10px; border-radius: 5px; margin-bottom: 15px; line-height: 1.4;'>
        <div style='font-size: 15px; font-weight: bold; color: #0056b3; margin-bottom: 3px;'>🟢 {u_name} 님 로그인 중</div>
        <div style='font-size: 14px; color: #333; margin-bottom: 2px;'>🏢 거점: {u_hub}</div>
        <div style='font-size: 14px; color: #333; margin-bottom: 2px;'>🏫 소속: {school_text}</div>
        <div style='font-size: 14px; color: #333;'>🛡️ 권한: {u_role}</div>
    </div>
    """, unsafe_allow_html=True)
    
    if st.sidebar.button("로그아웃", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.user_info = None
        st.session_state.current_page = "main"
        if "session_token" in st.query_params: del st.query_params["session_token"]
        st.rerun()
else:
    auth_choice = st.sidebar.radio("원하는 작업을 선택하세요", ["회원가입", "로그인"])
    users = load_json(USERS_FILE, {})
    
    if auth_choice == "회원가입":
        st.sidebar.subheader("📝 회원가입")
        reg_hub = st.sidebar.selectbox("거점학교 선택", HUB_SCHOOLS)
        reg_role = st.sidebar.selectbox("자격 선택", ["학생", "교사"])
        if reg_role == "학생": 
            reg_school = st.sidebar.text_input("소속 학교(원적교)", placeholder="예: 무룡고등학교")
            reg_class = st.sidebar.selectbox("소속 분반", CLASS_GROUPS)
        else: 
            reg_school = "교사소속"; reg_class = "교사"
            
        reg_id = st.sidebar.text_input("학번/ID 입력")
        reg_name = st.sidebar.text_input("이름 입력")
        reg_pw = st.sidebar.text_input("비밀번호", type="password")
        
        if st.sidebar.button("가입 신청", type="primary", use_container_width=True):
            if reg_role and reg_id and reg_pw and reg_name and reg_school:
                user_key = f"{reg_school}_{reg_id}" if reg_role == "학생" else f"teacher_{reg_id}"
                with db_lock:
                    fresh_users = load_json(USERS_FILE, {}) 
                    if user_key in fresh_users: 
                        st.sidebar.error("❌ 해당 학번/ID가 이미 존재합니다.")
                    else:
                        fresh_users[user_key] = {"id": reg_id, "password": reg_pw, "name": reg_name, "role": reg_role, "school": reg_school if reg_role == "학생" else "소속없음", "class_group": reg_class, "approved": False, "hub_school": reg_hub}
                        save_json(USERS_FILE, fresh_users)
                        st.sidebar.success("🎉 가입 완료! 관리자의 승인을 기다려주세요.")
            else: st.sidebar.warning("⚠️ 모든 빈칸을 빠짐없이 입력해주세요.")
                
    elif auth_choice == "로그인":
        login_hub = st.sidebar.selectbox("접속할 거점학교", HUB_SCHOOLS)
        login_type = st.sidebar.radio("로그인 계정 유형", ["학생", "교사(관리자)"])
        if login_type == "학생": login_school = st.sidebar.text_input("소속 학교(원적교)", placeholder="예: 무룡고등학교")
        else: login_school = ""
            
        input_id = st.sidebar.text_input("학번/ID")
        input_pw = st.sidebar.text_input("비밀번호", type="password")
        
        if st.sidebar.button("로그인", type="primary", use_container_width=True):
            if login_type == "교사(관리자)" and input_id in ADMIN_ACCOUNTS and input_pw == ADMIN_ACCOUNTS[input_id]["pw"]:
                admin_info = ADMIN_ACCOUNTS[input_id]
                st.session_state.logged_in = True
                st.session_state.user_info = {"user_key": input_id, "username": input_id, "name": admin_info["name"], "role": "관리자", "school": admin_info["school"], "class_group": "관리자", "hub_school": login_hub}
                st.query_params["session_token"] = encode_token(input_id, login_hub)
                st.rerun()
            else:
                user_key = f"{login_school}_{input_id}" if login_type == "학생" else f"teacher_{input_id}"
                if user_key in users and users[user_key].get("password") == input_pw:
                    db_role = users[user_key].get("role")
                    if (login_type == "학생" and db_role == "학생") or (login_type == "교사(관리자)" and db_role == "교사"):
                        if users[user_key].get("hub_school", "호계고등학교") == login_hub:
                            if users[user_key].get("approved", True):
                                st.session_state.logged_in = True
                                st.session_state.user_info = {"user_key": user_key, "username": users[user_key].get("id", input_id), "name": users[user_key].get("name", "이름없음"), "role": db_role, "school": users[user_key].get("school", "소속없음"), "class_group": users[user_key].get("class_group", "미배정"), "hub_school": login_hub}
                                st.query_params["session_token"] = encode_token(user_key, login_hub)
                                st.rerun()
                            else: st.sidebar.warning("⏳ 관리자(교사)의 가입 승인을 대기 중입니다.")
                        else: st.sidebar.error("❌ 선택하신 거점학교에 등록된 계정이 아닙니다.")
                    else: st.sidebar.error("❌ 가입하신 계정 유형이 다릅니다.")
                else: st.sidebar.error("❌ 학교, 학번/ID 또는 비밀번호가 틀렸습니다.")

st.sidebar.markdown("---")
# 💡 [요청 반영 5] 사이드바 푸터 '신선여자고등학교 김명남'으로 변경
st.sidebar.markdown("<div style='text-align: center; color: #222; font-size: 15px; font-weight: 900;'>🧑‍💻 만든 이:<br><span style='font-size: 20px; color: #000;'>신선여자고등학교 김명남</span></div>", unsafe_allow_html=True)

# --- [5] 화면 분기 로직 ---
if not st.session_state.logged_in:
    st.title("🏫 주제 탐구 캠프 학습 시스템")
    st.info("왼쪽 사이드바를 이용해 로그인해주세요.")

else:
    current_role = st.session_state.user_info["role"]
    current_user_key = st.session_state.user_info["user_key"]
    current_hub = st.session_state.user_info.get("hub_school", "호계고등학교")
    app_config = load_json(CONFIG_FILE, {})
    learning_data = load_json(DATA_FILE, {})

    if st.session_state.current_page in ACTIVITIES:
        act_name = st.session_state.current_page
        st.title(f"📄 {act_name}")
        st.markdown("---")
        
        if current_role == "학생":
            st.warning("🚨 [임시 데이터 저장 안내] 작성 중 화면 바깥을 클릭하면 임시로 남아있지만, 새로고침 시 날아갈 수 있습니다. 완전히 저장하려면 반드시 맨 아래의 **[제출 및 저장]** 버튼을 틈틈이 눌러주세요!")
            if act_name == ACTIVITIES[0]: render_activity1_form(current_user_key)
            elif act_name == ACTIVITIES[1]: render_activity2_form(current_user_key)
            elif act_name == ACTIVITIES[2]: render_activity3_form(current_user_key)
            elif act_name == ACTIVITIES[3]: render_activity4_form(current_user_key)
            elif act_name == ACTIVITIES[4]: render_activity5_form(current_user_key)
            elif act_name == ACTIVITIES[5]: render_activity6_form(current_user_key)
            elif act_name == ACTIVITIES[6]: render_activity7_form(current_user_key)
            elif act_name == ACTIVITIES[7]: render_activity8_form(current_user_key)
            elif act_name == ACTIVITIES[8]: render_activity9_form(current_user_key)
        else: st.warning("교사/관리자는 메인 화면의 '제출 모니터링 탭'을 이용해주세요.")
        
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown('<div class="back-btn-wrapper"></div>', unsafe_allow_html=True)
        if st.button("⬅️ 메인 화면으로 돌아가기", key="btn_back_bottom", use_container_width=True):
            st.session_state.current_page = "main"; st.rerun()

    elif st.session_state.current_page == "main":
        if current_role == "학생":
            st.info("💡 **AI 도구(Gemini 등) 활용 팁:** 아래 버튼을 눌러 나의 전체 활동 내역을 다운로드한 뒤, AI 채팅창에 파일을 끌어다 넣고 **'이 내용을 바탕으로 발표 자료 목차를 짜줘'**라고 질문해 보세요!")
            current_data = load_json(DATA_FILE, {})
            student_answers = current_data.get(current_user_key, {})
            full_html = generate_html_report(st.session_state.user_info, student_answers, target_act=None, app_config=app_config)
            st.download_button(label="📥 나의 전체 활동 포트폴리오 다운로드 (HTML)",
                               data=full_html.encode('utf-8-sig'),
                               file_name=f"{st.session_state.user_info['name']}_전체포트폴리오.html",
                               mime="text/html",
                               type="primary",
                               use_container_width=True)
            st.markdown("---")

            tabs_list = ["📌 캠프 공지 및 자료실"] + app_config["tabs"]
            tabs_objects = st.tabs(tabs_list)
            with tabs_objects[0]: render_camp_overview(current_role, current_hub)
            for index, tab_name in enumerate(app_config["tabs"]):
                with tabs_objects[index + 1]:
                    st.subheader(f"📘 {tab_name} 활동 및 자료 제출")
                    display_pdf(app_config["pdfs"].get(tab_name, f"{tab_name}.pdf"))
                    st.markdown("---")
                    questions = app_config["questions"].get(tab_name, [])
                    
                    st.markdown("<div style='color:#555; margin-bottom:10px;'>아래 질문들에 대한 답변을 텍스트로 작성한 후, 맨 아래의 <b>[제출 및 저장하기]</b> 버튼을 눌러주세요.</div>", unsafe_allow_html=True)
                    ans_dict = {}
                    for q in questions:
                        q_id = q["id"]
                        q_label = q["label"]
                        ans_data = learning_data.get(current_user_key, {}).get(tab_name, {}).get(q_id, {})
                        existing_text = ans_data.get("text", "") if isinstance(ans_data, dict) else ans_data
                        
                        st.markdown(f"**{q_label}**")
                        ans_dict[q_id] = st.text_area("내용 작성", value=existing_text, height=150, key=f"text_{current_user_key}_{tab_name}_{q_id}", label_visibility="collapsed")
                        st.markdown("<br>", unsafe_allow_html=True)
                    
                    if st.button("제출 및 저장하기", key=f"btn_tab_{tab_name}", type="primary"):
                        with db_lock:
                            fresh_data = load_json(DATA_FILE, {}) 
                            if current_user_key not in fresh_data: fresh_data[current_user_key] = {}
                            if tab_name not in fresh_data[current_user_key]: fresh_data[current_user_key][tab_name] = {}
                            for q_id, text_val in ans_dict.items():
                                fresh_data[current_user_key][tab_name][q_id] = {"text": text_val, "link": "", "file_name": "", "file_path": ""}
                            save_json(DATA_FILE, fresh_data)
                        st.toast(f"💾 {tab_name} 자료가 성공적으로 저장되었습니다!")
                        st.rerun()

        elif current_role in ["교사", "관리자"]:
            st.title(f"🛠️ {current_role} 대시보드 ({current_hub})")
            if current_role == "관리자": menu_tabs = st.tabs(["📌 캠프 공지(미리보기)", "👥 회원 관리", "🗂️ 차시 및 자료 편집", "📥 학생 제출 자료 조회 및 관리"])
            else: menu_tabs = st.tabs(["📌 캠프 공지(미리보기)", "👥 회원 관리", "📥 학생 제출 자료 조회 및 관리"])
            
            with menu_tabs[0]: render_camp_overview(current_role, current_hub)

            with menu_tabs[1]:
                all_users = load_json(USERS_FILE, {})
                hub_users = {k: v for k, v in all_users.items() if v.get("role") == "관리자" or v.get("hub_school", "호계고등학교") == current_hub}
                pending_users = {k: v for k, v in hub_users.items() if not v.get("approved", True)}
                approved_users = {k: v for k, v in hub_users.items() if v.get("approved", True)}

                st.subheader("⏳ 가입 승인 대기 목록")
                if pending_users:
                    df_pending = pd.DataFrame([{"학교": info.get("school", "-"), "학번/ID": info.get("id", k.split('_')[-1]), "이름": info.get("name", "이름없음"), "권한": info.get("role", "-"), "반": info.get("class_group", "-")} for k, info in pending_users.items()])
                    st.dataframe(df_pending, use_container_width=True)
                    col_app1, col_app2 = st.columns(2)
                    with col_app1:
                        approve_target = st.selectbox("승인할 회원을 선택하세요", ["선택"] + list(pending_users.keys()), format_func=lambda x: x if x == "선택" else f"[{pending_users[x].get('school', '소속없음')}] {pending_users[x].get('name', '이름없음')} ({pending_users[x].get('id', x.split('_')[-1])})")
                        if approve_target != "선택":
                            if st.button("✅ 선택한 회원 가입 승인", type="primary"):
                                with db_lock:
                                    fresh_users = load_json(USERS_FILE, {})
                                    if approve_target in fresh_users:
                                        fresh_users[approve_target]["approved"] = True
                                        save_json(USERS_FILE, fresh_users)
                                st.success("승인 완료!"); st.rerun()
                    with col_app2:
                        if st.button("✅ 대기 중인 모든 회원 일괄 승인", type="primary"):
                            with db_lock:
                                fresh_users = load_json(USERS_FILE, {})
                                for uid in pending_users.keys(): 
                                    if uid in fresh_users:
                                        fresh_users[uid]["approved"] = True
                                save_json(USERS_FILE, fresh_users)
                            st.success("일괄 승인 완료!"); st.rerun()
                else: st.info("가입 승인을 대기 중인 회원이 없습니다.")

                st.markdown("---")
                st.subheader("✅ 기존 승인된 회원 목록 및 관리")
                df_users = pd.DataFrame([{
                    "학교": info.get("school", "-"), "학번/ID": info.get("id", uid.split('_')[-1]), 
                    "이름": info.get("name", "이름없음"), "권한": info.get("role", "-"), 
                    "반": info.get("class_group", "-"), "비밀번호": info.get("password", "-")
                } for uid, info in approved_users.items()])
                st.dataframe(df_users, use_container_width=True)

                if current_role == "관리자":
                    st.markdown("---")
                    st.subheader("⚙️ 개별 회원 제어")
                    col1, col2 = st.columns(2)
                    editable_users = [u for u in approved_users.keys() if u not in ADMIN_ACCOUNTS]
                    with col1:
                        st.write("❌ **회원 강제 탈퇴(삭제)**")
                        delete_target = st.selectbox("삭제할 회원을 선택하세요", ["선택"] + editable_users, format_func=lambda x: x if x == "선택" else f"[{all_users[x].get('school', '소속없음')}] {all_users[x].get('name', '이름없음')} ({all_users[x].get('id', x.split('_')[-1])})")
                        if delete_target != "선택":
                            if st.button(f"⚠️ {all_users[delete_target].get('name', '해당 사용자')} 회원 데이터 영구 삭제", type="primary"):
                                with db_lock:
                                    fresh_users = load_json(USERS_FILE, {})
                                    if delete_target in fresh_users:
                                        del fresh_users[delete_target]
                                        save_json(USERS_FILE, fresh_users)
                                st.success("삭제 완료"); st.rerun()
                    with col2:
                        st.write("🔑 **학생/교사 비밀번호 강제 변경**")
                        pw_target = st.selectbox("비밀번호를 변경할 회원을 선택하세요", ["선택"] + editable_users, format_func=lambda x: x if x == "선택" else f"[{all_users[x].get('school', '소속없음')}] {all_users[x].get('name', '이름없음')} ({all_users[x].get('id', x.split('_')[-1])})")
                        new_pw = st.text_input("새로운 비밀번호 입력", type="password")
                        if pw_target != "선택":
                            if st.button("비밀번호 변경 적용", type="primary") and new_pw:
                                with db_lock:
                                    fresh_users = load_json(USERS_FILE, {})
                                    if pw_target in fresh_users:
                                        fresh_users[pw_target]["password"] = new_pw
                                        save_json(USERS_FILE, fresh_users)
                                st.success("비밀번호 성공적으로 변경"); st.rerun()

            if current_role == "관리자":
                with menu_tabs[2]:
                    st.subheader("👨‍🏫 교사용 특강 자료 업로드 (PPT, PDF, 외부 링크)")
                    with st.form("upload_lecture_material"):
                        mat_title = st.text_input("자료 제목 (예: 1일차 오리엔테이션 PPT)")
                        mat_type = st.radio("자료 유형 선택", ["파일 업로드 (PPT, PDF 등)", "외부 링크 (Notion, Google Docs 등)"])
                        mat_link = st.text_input("외부 링크인 경우 URL을 입력하세요", placeholder="https://...")
                        mat_file = st.file_uploader("파일인 경우 이곳에 업로드하세요")
                        if st.form_submit_button("자료 등록하여 공지사항에 올리기", type="primary"):
                            if not mat_title: st.error("자료 제목을 입력해주세요.")
                            else:
                                new_mat = {"id": f"mat_{datetime.datetime.now().strftime('%d%H%M%S')}", "title": mat_title}
                                if mat_type == "외부 링크 (Notion, Google Docs 등)": new_mat["type"] = "link"; new_mat["content"] = mat_link
                                else:
                                    if mat_file is not None:
                                        safe_filename = mat_file.name.replace("/", "_").replace("\\", "_")
                                        file_path = os.path.join(UPLOAD_DIR, f"lecture_{safe_filename}")
                                        with open(file_path, "wb") as f: f.write(mat_file.getvalue())
                                        new_mat["type"] = "file"; new_mat["content"] = file_path; new_mat["filename"] = mat_file.name
                                    else: st.error("파일을 선택해주세요."); st.stop()
                                
                                with db_lock:
                                    fresh_config = load_json(CONFIG_FILE, {})
                                    if "materials" not in fresh_config: fresh_config["materials"] = []
                                    fresh_config["materials"].append(new_mat)
                                    save_json(CONFIG_FILE, fresh_config)
                                st.success("등록 완료!"); st.rerun()
                    
                    current_materials = load_json(CONFIG_FILE, {}).get("materials", [])
                    if current_materials:
                        st.write("🗑️ **등록된 강의 자료 삭제**")
                        del_mat_target = st.selectbox("삭제할 자료를 선택하세요", options=current_materials, format_func=lambda x: x.get("title", "제목없음"))
                        if st.button("선택한 자료 삭제하기", type="primary"):
                            with db_lock:
                                fresh_config = load_json(CONFIG_FILE, {})
                                if "materials" in fresh_config and del_mat_target in fresh_config["materials"]:
                                    fresh_config["materials"].remove(del_mat_target)
                                    save_json(CONFIG_FILE, fresh_config)
                            st.success("삭제 완료!"); st.rerun()
                    
                    st.markdown("---")
                    st.subheader("⚙️ 차시(Tab) 동적 제어")
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write("➕ **새로운 학습 차시 추가**")
                        new_tab_name = st.text_input("추가할 차시 이름 입력")
                        new_pdf_name = st.text_input("연결할 PDF 파일명", value="session_new.pdf")
                        if st.button("차시 개설하기", type="primary"):
                            with db_lock:
                                fresh_config = load_json(CONFIG_FILE, {})
                                if new_tab_name and new_tab_name not in fresh_config["tabs"]:
                                    fresh_config["tabs"].append(new_tab_name)
                                    fresh_config["pdfs"][new_tab_name] = new_pdf_name
                                    fresh_config["questions"][new_tab_name] = []
                                    save_json(CONFIG_FILE, fresh_config)
                            st.success(f"🎉 {new_tab_name} 개설 완료."); st.rerun()
                    with col2:
                        st.write("❌ **기존 학습 차시 폐쇄**")
                        del_tab_target = st.selectbox("삭제할 차시를 지정하세요", ["선택"] + app_config["tabs"])
                        if del_tab_target != "선택":
                            if st.button(f"🔥 {del_tab_target} 세션 및 질문 전체 삭제", type="primary"):
                                with db_lock:
                                    fresh_config = load_json(CONFIG_FILE, {})
                                    if del_tab_target in fresh_config["tabs"]:
                                        fresh_config["tabs"].remove(del_tab_target)
                                        fresh_config["pdfs"].pop(del_tab_target, None)
                                        fresh_config["questions"].pop(del_tab_target, None)
                                        save_json(CONFIG_FILE, fresh_config)
                                st.success(f"삭제 완료."); st.rerun()
                                
                    st.markdown("---")
                    st.subheader("📝 차시별 제출 텍스트 상자(질문 문항) 동적 가변 설정")
                    target_q_tab = st.selectbox("문항을 편집할 차시 선택", app_config["tabs"])
                    if target_q_tab:
                        current_qs = app_config["questions"].get(target_q_tab, [])
                        for q in current_qs: st.text(f" - [{q['id']}] {q.get('label', '')}")
                        q_col1, q_col2 = st.columns(2)
                        with q_col1:
                            add_q_label = st.text_input("질문 설명(라벨) 문구 입력")
                            if st.button("질문 추가", type="primary") and add_q_label:
                                with db_lock:
                                    fresh_config = load_json(CONFIG_FILE, {})
                                    new_id = f"q_{datetime.datetime.now().strftime('%d%H%M%S')}"
                                    if target_q_tab in fresh_config["questions"]:
                                        fresh_config["questions"][target_q_tab].append({"id": new_id, "label": add_q_label})
                                        save_json(CONFIG_FILE, fresh_config)
                                st.success("문항 추가 완료."); st.rerun()
                        with q_col2:
                            if current_qs:
                                del_q_target = st.selectbox("삭제할 문항 고르기", options=current_qs, format_func=lambda x: x.get("label", ""))
                                if st.button("선택한 문항 삭제", type="primary"):
                                    with db_lock:
                                        fresh_config = load_json(CONFIG_FILE, {})
                                        if target_q_tab in fresh_config["questions"]:
                                            current_list = fresh_config["questions"][target_q_tab]
                                            fresh_config["questions"][target_q_tab] = [q for q in current_list if q["id"] != del_q_target["id"]]
                                            save_json(CONFIG_FILE, fresh_config)
                                    st.success("삭제 완료."); st.rerun()

            with menu_tabs[-1]:
                col_title, col_btn = st.columns([8, 2])
                with col_title:
                    st.subheader("📥 학생 학습 활동 및 제출 자료 실시간 조회")
                with col_btn:
                    if st.button("🔄 실시간 새로고침", type="primary", use_container_width=True):
                        st.rerun()
                
                all_users = load_json(USERS_FILE, {})
                learning_data = load_json(DATA_FILE, {})
                
                filter_class = st.radio("조회할 반 선택", ["전체 보기"] + CLASS_GROUPS, horizontal=True)
                
                student_list = [uid for uid, info in all_users.items() if info.get("role") == "학생" and info.get("hub_school", "호계고등학교") == current_hub and (filter_class == "전체 보기" or filter_class == info.get("class_group", "미배정"))]
                
                if not student_list: st.info(f"선택하신 조건({filter_class})에 해당하는 가입 학생이 없습니다.")
                else:
                    view_mode = st.radio("조회 모드 선택", ["👤 특정 학생 실시간 집중 분석", "📅 항목별(활동지/차시) 전체 현황 (엑셀 다운로드)"], horizontal=True)
                    st.markdown("---")
                    
                    if view_mode == "👤 특정 학생 실시간 집중 분석":
                        selected_student = st.selectbox("학생 선택", student_list, format_func=lambda x: f"[{all_users[x].get('class_group', '-')}] {all_users[x].get('school', '-')} {all_users[x].get('name', '이름없음')} ({all_users[x].get('id', x.split('_')[-1])})")
                        if selected_student:
                            student_answers = learning_data.get(selected_student, {})
                            
                            u_info_for_html = all_users[selected_student]
                            u_info_for_html['username'] = selected_student.split('_')[-1]
                            html_content = generate_html_report(u_info_for_html, student_answers, target_act=None, app_config=app_config)
                            
                            st.download_button(label=f"📄 {all_users[selected_student].get('name', '학생')} 포트폴리오 일괄 다운로드 (웹문서)", data=html_content.encode('utf-8-sig'), file_name=f"{all_users[selected_student].get('name', '학생')}_학습포트폴리오.html", mime="text/html", type="primary")
                            
                            # 💡 [요청 반영 1] 다운로드 버튼 아래, 학생 화면을 100% 그대로 살려주는 라이브 뷰 구현
                            st.markdown("---")
                            st.subheader("👀 선택한 학생의 실시간 작성 내용 조회")
                            
                            st.markdown("#### 📍 [1] 활동지 작성 내역")
                            for act in ACTIVITIES:
                                ans = student_answers.get(act, {})
                                if not ans: continue
                                
                                if act == ACTIVITIES[0] and ans.get("is_custom"):
                                    st.markdown(f"### **{act}**"); st.markdown("**[1단계] 추출한 핵심 내용 요소**"); st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                    st.markdown("**[2단계] 생기부 탐구 내용 분석**"); st.dataframe(pd.DataFrame(ans.get("df2", [])), use_container_width=True)
                                    st.markdown("**[3단계] 탐구 주제 및 문제 인식**"); st.info(ans.get("step3", "-")); st.markdown("<br>", unsafe_allow_html=True)
                                elif act == ACTIVITIES[1] and ans.get("is_custom_act2"):
                                    st.markdown(f"### **{act}**"); st.markdown("**[1단계] 관심에서 출발하기**"); st.write(f"- 관심 현상: {ans.get('step1_1', '')}\n- 계기: {ans.get('step1_2', '')}")
                                    st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True); st.markdown("**[2단계] 교과 개념에 닻 내리기**"); st.dataframe(pd.DataFrame(ans.get("df2", [])), use_container_width=True)
                                    st.write(f"- 잠정적 탐구 방향: {ans.get('step2_dir', '')}"); st.markdown("**[3단계] 주제 적절성 자가 진단**"); st.dataframe(pd.DataFrame(ans.get("df3", [])), use_container_width=True)
                                    st.markdown("**[4단계] 학술적 질문으로 바꾸기**"); st.write(f"- 선택 렌즈: {ans.get('step4_1', '')}\n- 탐구 질문: {ans.get('step4_2', '')}")
                                    st.markdown("**[5단계] 탐구 전략 하나 정하기**"); st.write(f"- 선택 전략: {ans.get('step5_1', '')}\n- 구체적 방법: {ans.get('step5_2', '')}")
                                    st.markdown("**[6단계] AI 멘토에게 점검받기**"); st.info(f"반영할 점: {ans.get('step6', '')}"); st.markdown("**[7단계] 나의 탐구 계획 완성하기**")
                                    st.dataframe(pd.DataFrame(ans.get("df7", [])), use_container_width=True); st.write(f"**요약:** 나는 ({ans.get('step7_s1', '')}) 개념을 활용해, ({ans.get('step7_s2', '')}) 전략으로 ({ans.get('step7_s3', '')})을(를) 밝히려 한다."); st.markdown("<br>", unsafe_allow_html=True)
                                elif act in [ACTIVITIES[2], ACTIVITIES[5], ACTIVITIES[6]] and ans.get("is_custom_feedback"):
                                    st.markdown(f"### **{act}**"); st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                elif act == ACTIVITIES[3] and ans.get("is_custom_refs"):
                                    st.markdown(f"### **{act}**"); st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                elif act == ACTIVITIES[4] and ans.get("is_custom_act5"):
                                    st.markdown(f"### **{act}**"); st.markdown("#### 1. 기본 정보")
                                    info_df = pd.DataFrame([{"항목": "교과명(강의명)", "내용": ans.get("info_course", ""), "항목2": "탐구 기간", "내용2": ans.get("info_date", "")}, {"항목": "소속학교", "내용": ans.get("info_school", ""), "항목2": "진로 희망", "내용2": ans.get("info_career", "")}, {"항목": "학번/이름", "내용": ans.get("info_name", ""), "항목2": "관련 교과, 단원", "내용2": ans.get("info_subject", "")}, {"항목": "탐구 방법", "내용": ans.get("info_method", ""), "항목2": "탐구 주제", "내용2": ans.get("info_topic", "")}])
                                    st.dataframe(info_df, hide_index=True, use_container_width=True); st.markdown("#### 2. 탐구 개요")
                                    st.write(f"**가. 탐구 주제:** {ans.get('topic_title', '')}"); st.write(f"**나. 탐구 동기 및 배경**"); st.write(f"1) 교과 연계 동기: {ans.get('motive_1', '')}\n2) 선정 배경: {ans.get('motive_2', '')}\n3) 탐구 목적: {ans.get('purpose', '')}"); st.write("4) 이론적 배경:")
                                    st.dataframe(pd.DataFrame(ans.get("bg_df", [])), hide_index=True, use_container_width=True); st.markdown("#### 3. 탐구 설계 및 내용")
                                    st.write(f"**가. 탐구 방법:** {', '.join(ans.get('selected_methods', []))}"); st.write("**나. 세부 절차:**")
                                    st.dataframe(pd.DataFrame(ans.get("proc_df", [])), hide_index=True, use_container_width=True); st.write("**다. 탐구 내용 (본론):**"); st.info(ans.get("content_body", ""))
                                    st.write(f"**라. 탐구 결과**\n1) 결과 요약: {ans.get('result_summary', '')}\n2) 해석 및 의의: {ans.get('result_meaning', '')}"); st.markdown("#### 4. 결론 및 제언")
                                    st.write(f"**가. 결론:** {ans.get('conclusion', '')}"); st.write(f"**나. 성찰**\n1) 배우고 느낀 점: {ans.get('reflection_1', '')}\n2) 한계점: {ans.get('reflection_2', '')}"); st.write(f"**다. 후속 활동:** {ans.get('next_step', '')}")
                                    st.markdown("#### 5. 참고 문헌"); st.write(f"**가. 논문/도서:** {ans.get('ref_book', '')}\n**나. 웹사이트/기사:** {ans.get('ref_web', '')}"); st.markdown("<br>", unsafe_allow_html=True)
                                elif act == ACTIVITIES[7] and ans.get("is_custom_self_eval"):
                                    st.markdown(f"### **{act}**"); st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                elif act == ACTIVITIES[8] and ans.get("is_custom_roadmap"):
                                    st.markdown(f"### **{act}**"); st.markdown("**1. 꼬리에 꼬리를 무는 독서**"); st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                    st.markdown("**2. 나만의 3개년 실천 로드맵**"); st.dataframe(pd.DataFrame(ans.get("df2", [])), use_container_width=True)
                                else:
                                    ans_content = ans.get("content", {})
                                    if isinstance(ans_content, str): ans_content = {"text": ans_content}
                                    if ans_content.get("text") or ans_content.get("link"):
                                        st.markdown(f"**{act}**")
                                        if ans_content.get("text"): st.write(f"📝 {ans_content['text']}")
                                        if ans_content.get("link"): st.write(f"🔗 {ans_content['link']}")
                            
                            st.markdown("---")
                            st.markdown("#### 📍 [2] 차시별 제출 자료")
                            for t_name in app_config["tabs"]:
                                for q in app_config["questions"].get(t_name, []):
                                    ans = student_answers.get(t_name, {}).get(q["id"], {})
                                    if isinstance(ans, str): ans = {"text": ans} 
                                    if ans.get("text"):
                                        st.markdown(f"**[{t_name}] {q.get('label', '')}**")
                                        st.write(f"📝 {ans['text']}")
                                        st.markdown("<br>", unsafe_allow_html=True)

                    elif view_mode == "📅 항목별(활동지/차시) 전체 현황 (엑셀 다운로드)":
                        combined_list = ["--- [활동지 데이터 목록] ---"] + ACTIVITIES + ["--- [학습 차시 데이터 목록] ---"] + app_config["tabs"]
                        selected_view = st.selectbox("다운로드할 데이터 범주를 선택하세요", combined_list)
                        
                        if selected_view == ACTIVITIES[0]:
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            csv_data = []
                            for s_uid in student_list:
                                ans = learning_data.get(s_uid, {}).get(selected_view, {})
                                u_info = all_users[s_uid]
                                st.markdown(f"#### 👤 [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})")
                                st.markdown("**[1단계] 학과/전공 가이드북 읽고 핵심 내용 요소 추출하기**")
                                st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                st.markdown("**[2단계] 내용 요소 중심 학교생활기록부 탐구 내용 분석하기**")
                                st.dataframe(pd.DataFrame(ans.get("df2", [])), use_container_width=True)
                                st.markdown("**[3단계] 탐구 주제 및 문제 인식**")
                                st.info(ans.get("step3", "-")); st.markdown("---")
                                
                                csv_data.append([f"■ [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})", "", "", "", "", ""])
                                csv_data.append(["[1단계] 학과/전공 가이드북 읽고 핵심 내용 요소 추출하기", "", "", "", "", ""])
                                csv_data.append(["학과/전공명", "핵심 내용 요소", "", "", "", ""])
                                for row in ans.get("df1", []): csv_data.append([row.get("학과/전공명", ""), row.get("핵심 내용 요소", ""), "", "", "", ""])
                                csv_data.append(["", "", "", "", "", ""])
                                csv_data.append(["[2단계] 내용 요소 중심 학교생활기록부 탐구 내용 분석하기", "", "", "", "", ""])
                                csv_data.append(["구분", "키워드1", "키워드2", "키워드3", "키워드4", "키워드5"])
                                for row in ans.get("df2", []): csv_data.append([row.get("구분", ""), row.get("키워드1", ""), row.get("키워드2", ""), row.get("키워드3", ""), row.get("키워드4", ""), row.get("키워드5", "")])
                                csv_data.append(["", "", "", "", "", ""])
                                csv_data.append(["[3단계] 이번 특강을 통해 탐구하고 싶은 내용 영역 또는 문제 인식", "", "", "", "", ""])
                                csv_data.append([ans.get("step3", ""), "", "", "", "", ""])
                                csv_data.append(["==================================================", "", "", "", "", ""])
                                csv_data.append(["", "", "", "", "", ""])
                            df_csv = pd.DataFrame(csv_data)
                            st.download_button(f"📊 {selected_view[:6]} 엑셀 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{selected_view[:6]}_{filter_class}_결과.csv", mime='text/csv', type="primary")

                        elif selected_view == ACTIVITIES[1]:
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            csv_data = []
                            for s_uid in student_list:
                                ans = learning_data.get(s_uid, {}).get(selected_view, {})
                                u_info = all_users[s_uid]
                                st.markdown(f"#### 👤 [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})")
                                st.write(f"- 관심 현상: {ans.get('step1_1', '')}\n- 계기: {ans.get('step1_2', '')}")
                                st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True); st.dataframe(pd.DataFrame(ans.get("df2", [])), use_container_width=True)
                                st.write(f"- 잠정적 탐구 방향: {ans.get('step2_dir', '')}"); st.dataframe(pd.DataFrame(ans.get("df3", [])), use_container_width=True)
                                st.write(f"- 선택 렌즈: {ans.get('step4_1', '')}\n- 탐구 질문: {ans.get('step4_2', '')}"); st.write(f"- 선택 전략: {ans.get('step5_1', '')}\n- 구체적 방법: {ans.get('step5_2', '')}")
                                st.info(f"반영할 점: {ans.get('step6', '')}"); st.dataframe(pd.DataFrame(ans.get("df7", [])), use_container_width=True)
                                st.markdown("---")
                                
                                csv_data.append([f"■ [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})", "", "", ""])
                                csv_data.append(["[1단계] 관심에서 출발하기", "", "", ""])
                                csv_data.append([f"관심사: {ans.get('step1_1', '')}", f"계기: {ans.get('step1_2', '')}", "", ""])
                                csv_data.append(["이전 탐구 확장 시", "내용", "", ""])
                                for row in ans.get("df1", []): csv_data.append([row.get("구분", ""), row.get("내용", ""), "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[2단계] 교과 개념에 닻 내리기", "", "", ""])
                                csv_data.append(["구분", "내용", "", ""])
                                for row in ans.get("df2", []): csv_data.append([row.get("구분", ""), row.get("내용", ""), "", ""])
                                csv_data.append([f"잠정적 탐구 방향: {ans.get('step2_dir', '')}", "", "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[3단계] 주제 적절성 자가 진단", "", "", ""])
                                csv_data.append(["점검 항목", "점검 질문", "좋은 예", "판단 (O/X)"])
                                for row in ans.get("df3", []): csv_data.append([row.get("점검 항목", ""), row.get("점검 질문", ""), row.get("좋은 예", ""), row.get("판단 (O/X)", "")])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[4단계] 학술적 질문으로 바꾸기", "", "", ""])
                                csv_data.append([f"선택 렌즈: {ans.get('step4_1', '')}", f"핵심 탐구 질문: {ans.get('step4_2', '')}", "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[5단계] 탐구 전략 하나 정하기", "", "", ""])
                                csv_data.append([f"선택 전략: {ans.get('step5_1', '')}", f"접근 방법: {ans.get('step5_2', '')}", "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[6단계] AI 멘토에게 점검받기", "", "", ""])
                                csv_data.append([f"반영할 점: {ans.get('step6', '')}", "", "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[7단계] 나의 탐구 계획 완성하기", "", "", ""])
                                csv_data.append(["항목", "내용", "", ""])
                                for row in ans.get("df7", []): csv_data.append([row.get("항목", ""), row.get("내용", ""), "", ""])
                                csv_data.append([f"요약: 나는 ({ans.get('step7_s1', '')}) 개념을 활용해, ({ans.get('step7_s2', '')}) 전략으로 ({ans.get('step7_s3', '')})을(를) 밝히려 한다.", "", "", ""])
                                csv_data.append(["==================================================", "", "", ""])
                                csv_data.append(["", "", "", ""])
                            df_csv = pd.DataFrame(csv_data)
                            st.download_button(f"📊 {selected_view[:6]} 엑셀 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{selected_view[:6]}_{filter_class}_결과.csv", mime='text/csv', type="primary")

                        elif selected_view in [ACTIVITIES[2], ACTIVITIES[5], ACTIVITIES[6]]:
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            csv_data = []
                            for s_uid in student_list:
                                ans = learning_data.get(s_uid, {}).get(selected_view, {})
                                u_info = all_users[s_uid]
                                st.markdown(f"#### 👤 [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})")
                                st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                st.markdown("---")
                                csv_data.append([f"■ [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})", "", ""])
                                csv_data.append(["구분", "피드백 내용 (구체적으로)", "보완 및 수정 계획"])
                                for row in ans.get("df1", []): csv_data.append([row.get("구분", ""), row.get("피드백 내용 (구체적으로)", ""), row.get("보완 및 수정 계획", "")])
                                csv_data.append(["", "", ""])
                            df_csv = pd.DataFrame(csv_data)
                            st.download_button(f"📊 {selected_view[:6]} 엑셀 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{selected_view[:6]}_{filter_class}_결과.csv", mime='text/csv', type="primary")

                        elif selected_view == ACTIVITIES[3]:
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            csv_data = []
                            for s_uid in student_list:
                                ans = learning_data.get(s_uid, {}).get(selected_view, {})
                                u_info = all_users[s_uid]
                                st.markdown(f"#### 👤 [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})")
                                st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                st.markdown("---")
                                csv_data.append([f"■ [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})", "", "", ""])
                                csv_data.append(["사이트명", "제목", "내용", "선정이유"])
                                for row in ans.get("df1", []): csv_data.append([row.get("사이트명", ""), row.get("제목", ""), row.get("내용", ""), row.get("선정이유", "")])
                                csv_data.append(["", "", "", ""])
                            df_csv = pd.DataFrame(csv_data)
                            st.download_button(f"📊 {selected_view[:6]} 엑셀 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{selected_view[:6]}_{filter_class}_결과.csv", mime='text/csv', type="primary")

                        elif selected_view == ACTIVITIES[4]:
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            csv_data = []
                            for s_uid in student_list:
                                ans = learning_data.get(s_uid, {}).get(selected_view, {})
                                u_info = all_users[s_uid]
                                st.markdown(f"#### 👤 [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})")
                                
                                info_df = pd.DataFrame([
                                    {"항목": "교과명(강의명)", "내용": ans.get("info_course", ""), "항목2": "탐구 기간", "내용2": ans.get("info_date", "")},
                                    {"항목": "소속학교", "내용": ans.get("info_school", ""), "항목2": "진로 희망", "내용2": ans.get("info_career", "")},
                                    {"항목": "학번/이름", "내용": ans.get("info_name", ""), "항목2": "관련 교과, 단원", "내용2": ans.get("info_subject", "")},
                                    {"항목": "탐구 방법", "내용": ans.get("info_method", ""), "항목2": "탐구 주제", "내용2": ans.get("info_topic", "")}
                                ])
                                st.dataframe(info_df, hide_index=True, use_container_width=True)
                                
                                st.write(f"**가. 탐구 주제:** {ans.get('topic_title', '')}")
                                st.write(f"**나. 탐구 동기 및 배경**")
                                st.write(f"1) 교과 연계 동기: {ans.get('motive_1', '')}")
                                st.write(f"2) 선정 배경: {ans.get('motive_2', '')}")
                                st.write(f"3) 탐구 목적: {ans.get('purpose', '')}")
                                st.write(f"4) 이론적 배경:")
                                st.dataframe(pd.DataFrame(ans.get("bg_df", [])), hide_index=True, use_container_width=True)
                                
                                st.write(f"**가. 탐구 방법:** {', '.join(ans.get('selected_methods', []))}")
                                st.write(f"**나. 세부 절차:**")
                                st.dataframe(pd.DataFrame(ans.get("proc_df", [])), hide_index=True, use_container_width=True)
                                st.write(f"**다. 탐구 내용 (본론):**")
                                st.info(ans.get("content_body", ""))
                                
                                st.write(f"**라. 탐구 결과**\n1) 결과 요약: {ans.get('result_summary', '')}\n2) 해석 및 의의: {ans.get('result_meaning', '')}")
                                st.write(f"**가. 결론:** {ans.get('conclusion', '')}")
                                st.write(f"**나. 성찰**\n1) 배우고 느낀 점: {ans.get('reflection_1', '')}\n2) 한계점: {ans.get('reflection_2', '')}")
                                st.write(f"**다. 후속 활동:** {ans.get('next_step', '')}")
                                st.write(f"**가. 논문/도서:** {ans.get('ref_book', '')}\n**나. 웹사이트/기사:** {ans.get('ref_web', '')}")
                                st.markdown("---")
                                
                                csv_data.append([f"■ [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})", "", "", ""])
                                csv_data.append(["[1. 기본 정보]", "", "", ""])
                                csv_data.append(["교과명(강의명)", ans.get("info_course", ""), "탐구 기간", ans.get("info_date", "")])
                                csv_data.append(["소속학교", ans.get("info_school", ""), "진로 희망", ans.get("info_career", "")])
                                csv_data.append(["학번/이름", ans.get("info_name", ""), "관련 교과, 단원", ans.get("info_subject", "")])
                                csv_data.append(["탐구 방법", ans.get("info_method", ""), "탐구 주제", ans.get("info_topic", "")])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[2. 탐구 개요]", "", "", ""])
                                csv_data.append(["가. 탐구 주제", ans.get("topic_title", ""), "", ""])
                                csv_data.append(["나. 1) 교과 연계 동기", ans.get("motive_1", ""), "", ""])
                                csv_data.append(["나. 2) 선정 배경", ans.get("motive_2", ""), "", ""])
                                csv_data.append(["나. 3) 탐구 목적", ans.get("purpose", ""), "", ""])
                                csv_data.append(["나. 4) 이론적 배경", "", "", ""])
                                for row in ans.get("bg_df", []): csv_data.append([row.get("구분", ""), row.get("내용", ""), "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[3. 탐구 설계 및 내용]", "", "", ""])
                                csv_data.append(["가. 탐구 방법", ", ".join(ans.get("selected_methods", [])), "", ""])
                                csv_data.append(["나. 세부 절차 (순서/한 일)", "", "", ""])
                                for row in ans.get("proc_df", []): csv_data.append([row.get("순서", ""), row.get("한 일", ""), "", ""])
                                csv_data.append(["다. 탐구 내용 (본론)", ans.get("content_body", ""), "", ""])
                                csv_data.append(["라. 1) 결과 요약", ans.get("result_summary", ""), "", ""])
                                csv_data.append(["라. 2) 해석 및 의의", ans.get("result_meaning", ""), "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[4. 결론 및 제언]", "", "", ""])
                                csv_data.append(["가. 결론", ans.get("conclusion", ""), "", ""])
                                csv_data.append(["나. 1) 배우고 느낀 점", ans.get("reflection_1", ""), "", ""])
                                csv_data.append(["나. 2) 한계점", ans.get("reflection_2", ""), "", ""])
                                csv_data.append(["다. 후속 활동", ans.get("next_step", ""), "", ""])
                                csv_data.append(["", "", "", ""])
                                csv_data.append(["[5. 참고 문헌]", "", "", ""])
                                csv_data.append(["가. 논문/도서", ans.get("ref_book", ""), "", ""])
                                csv_data.append(["나. 웹사이트/기사", ans.get("ref_web", ""), "", ""])
                                csv_data.append(["==================================================", "", "", ""])
                                csv_data.append(["", "", "", ""])
                            df_csv = pd.DataFrame(csv_data)
                            st.download_button(f"📊 {selected_view[:6]} 엑셀 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{selected_view[:6]}_{filter_class}_결과.csv", mime='text/csv', type="primary")

                        elif selected_view == ACTIVITIES[7]:
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            csv_data = []
                            for s_uid in student_list:
                                ans = learning_data.get(s_uid, {}).get(selected_view, {})
                                u_info = all_users[s_uid]
                                st.markdown(f"#### 👤 [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})")
                                st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                st.markdown("---")
                                csv_data.append([f"■ [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})", ""])
                                csv_data.append(["항목", "내용"])
                                for row in ans.get("df1", []): csv_data.append([row.get("항목", ""), row.get("내용", "")])
                                csv_data.append(["", ""])
                            df_csv = pd.DataFrame(csv_data)
                            st.download_button(f"📊 {selected_view[:6]} 엑셀 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{selected_view[:6]}_{filter_class}_결과.csv", mime='text/csv', type="primary")

                        elif selected_view == ACTIVITIES[8]:
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            csv_data = []
                            for s_uid in student_list:
                                ans = learning_data.get(s_uid, {}).get(selected_view, {})
                                u_info = all_users[s_uid]
                                st.markdown(f"#### 👤 [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})")
                                st.markdown("**1. 꼬리에 꼬리를 무는 독서**"); st.dataframe(pd.DataFrame(ans.get("df1", [])), use_container_width=True)
                                st.markdown("**2. 나만의 3개년 실천 로드맵**"); st.dataframe(pd.DataFrame(ans.get("df2", [])), use_container_width=True)
                                st.markdown("---")
                                
                                csv_data.append([f"■ [{u_info.get('school', '')}] {u_info.get('class_group', '')} - {u_info.get('name', '')} ({u_info.get('id', s_uid.split('_')[-1])})", "", ""])
                                csv_data.append(["[1. 꼬리에 꼬리를 무는 독서]", "", ""])
                                csv_data.append(["구분", "도서명 / 저자", "선정 이유"])
                                for row in ans.get("df1", []): csv_data.append([row.get("구분", ""), row.get("도서명 / 저자", ""), row.get("선정 이유 (탐구 활동과의 연결고리)", "")])
                                csv_data.append(["", "", ""])
                                csv_data.append(["[2. 나만의 3개년 실천 로드맵]", "", ""])
                                csv_data.append(["시기", "중점 목표", "주요 활동 계획"])
                                for row in ans.get("df2", []): csv_data.append([row.get("시기", ""), row.get("중점 목표", ""), row.get("주요 활동 계획 (주제탐구, 독서, 실험 등)", "")])
                                csv_data.append(["", "", ""])
                            df_csv = pd.DataFrame(csv_data)
                            st.download_button(f"📊 {selected_view[:6]} 엑셀 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{selected_view[:6]}_{filter_class}_결과.csv", mime='text/csv', type="primary")

                        elif selected_view in app_config["tabs"]:
                            for q in app_config["questions"].get(selected_view, []):
                                st.markdown(f"##### ❓ {q.get('label', '')}")
                                q_summary_data = []
                                for s_uid in student_list:
                                    ans = learning_data.get(s_uid, {}).get(selected_view, {}).get(q["id"], {})
                                    if isinstance(ans, str): ans = {"text": ans}
                                    q_summary_data.append({"학교": all_users[s_uid].get("school", "-"), "반": all_users[s_uid].get("class_group", "-"), "학번": all_users[s_uid].get("id", s_uid.split('_')[-1]), "이름": all_users[s_uid].get("name", ""), "입력 텍스트": ans.get("text", "-")})
                                df_q = pd.DataFrame(q_summary_data)
                                st.dataframe(df_q, use_container_width=True, hide_index=True)
                                st.download_button(f"📊 문항 데이터 다운로드 ({filter_class})", data=df_q.to_csv(index=False).encode('utf-8-sig'), file_name=f"{selected_view}_{filter_class}_결과.csv", mime='text/csv', key=f"csv_{q['id']}")
