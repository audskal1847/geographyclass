import streamlit as st
import json
import os
import base64
import pandas as pd
import datetime
import threading

# --- [1] 파일 경로 설정 및 상수 정의 ---
USERS_FILE = "users.json"
DATA_FILE = "learning_data.json"
CONFIG_FILE = "config.json"
UPLOAD_DIR = "uploads" 

os.makedirs(UPLOAD_DIR, exist_ok=True)

# 📌 과목 및 반 목록 세팅 (요청사항 반영)
SUBJECTS = [
    "3학년 여행지리", 
    "2학년 도시의 미래 탐구"
]

CLASSES_MAP = {
    "3학년 여행지리": ["3B(3-6반)", "3A(3-8반)"],
    "2학년 도시의 미래 탐구": ["2G(2-1반)", "2H(2-2반)", "2I(2-8반)"]
}

# 관리자 계정 세팅
ADMIN_ACCOUNTS = {
    "admin": {"pw": "admin00", "name": "정현경(관리자)"}
}

# 📌 범용 수업용 핵심 활동지 3종
ACTIVITIES = [
    "[활동지1] 배움 노트 (수업 요약 및 질문)",
    "[활동지2] 수행평가/프로젝트 설계도",
    "[활동지3] 자기평가 및 후속 계획"
]

INFO_BOX = "<div style='background-color: #f0f4f8; padding: 15px; border-radius: 8px; font-size: 17px; font-weight: 600; color: #222; margin-bottom: 15px; border-left: 5px solid #0056b3; line-height: 1.5;'>{}</div>"

db_lock = threading.Lock()

# --- [2] 데이터 입출력 및 초기화 함수 ---
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
            import time
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
            if adm_id not in users or users[adm_id].get("password") != adm_info["pw"]:
                users[adm_id] = {
                    "id": adm_id, "password": adm_info["pw"], "name": adm_info["name"],
                    "role": "관리자", "subject": "전체", "class_group": "관리자", "approved": True
                }
                users_changed = True
        if users_changed: save_json(USERS_FILE, users)
        
        default_tabs = [f"{i}차시" for i in range(1, 4)]
        default_pdfs = {f"{i}차시": f"session{i}.pdf" for i in range(1, 4)}
        default_questions_template = [
            {"id": "q1", "label": "1. 오늘 배운 핵심 내용을 요약해보세요."},
            {"id": "q2", "label": "2. 질문이나 더 알아보고 싶은 점을 적어주세요."}
        ]
        default_questions = {tab: default_questions_template.copy() for tab in default_tabs}
        
        current_config = load_json(CONFIG_FILE, {})
        needs_update = False
        if "tabs" not in current_config:
            current_config["tabs"] = default_tabs
            current_config["pdfs"] = default_pdfs
            current_config["questions"] = default_questions
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
    else: st.info(f"💡 수업 자료 파일('{file_path}')이 폴더에 없습니다. 파일을 업로드하면 이곳에 표시됩니다.")

# --- [3] 활동지 렌더링 함수들 (범용 수업용 개편) ---
def render_activity1(user_key):
    category = ACTIVITIES[0]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.markdown(INFO_BOX.format("오늘 수업에서 배운 내용을 정리하고, 새롭게 생긴 질문이나 호기심을 기록합니다."), unsafe_allow_html=True)
    
    st.markdown("#### 1. 학습 개요")
    c1, c2 = st.columns(2)
    date = c1.date_input("학습 일자", value=pd.to_datetime(ans.get("date", datetime.date.today())))
    topic = c2.text_input("학습 주제", value=ans.get("topic", ""))
    
    st.markdown("#### 2. 핵심 키워드 및 요약")
    default_df1 = pd.DataFrame([{"핵심 키워드": "", "내용 요약": ""} for _ in range(3)])
    df1 = pd.DataFrame(ans.get("df1", default_df1.to_dict('records')))
    edited_df1 = st.data_editor(df1, num_rows="dynamic", use_container_width=True, key="act1_df1")
    
    st.markdown("#### 3. 질문 및 성찰")
    reflection = st.text_area("궁금한 점, 이해가 안 되는 부분, 또는 실생활에 적용해보고 싶은 아이디어를 적어보세요.", value=ans.get("reflection", ""), height=150)
    
    if st.button("활동지 저장하기", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {
                "date": str(date), "topic": topic, "df1": edited_df1.to_dict('records'), "reflection": reflection
            }
            save_json(DATA_FILE, current_data)
        st.toast("🎉 배움 노트가 저장되었습니다!")

def render_activity2(user_key):
    category = ACTIVITIES[1]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.markdown(INFO_BOX.format("수행평가나 프로젝트를 시작하기 전, 무엇을 어떻게 탐구할지 구체적인 계획을 세웁니다."), unsafe_allow_html=True)
    
    title = st.text_input("프로젝트/수행평가 주제", value=ans.get("title", ""))
    motive = st.text_area("주제 선정 동기 (수업 내용과 어떻게 연결되나요?)", value=ans.get("motive", ""))
    
    st.markdown("#### 세부 탐구(작업) 계획")
    default_df = pd.DataFrame([{"단계": f"{i}단계", "세부 계획 및 역할": "", "예상 소요시간": ""} for i in range(1, 4)])
    df = pd.DataFrame(ans.get("df", default_df.to_dict('records')))
    edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True, hide_index=True)
    
    outcome = st.text_area("최종 예상 결과물 (보고서, 발표자료, 포스터 등) 및 기대 효과", value=ans.get("outcome", ""))
    
    if st.button("설계도 저장하기", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {
                "title": title, "motive": motive, "df": edited_df.to_dict('records'), "outcome": outcome
            }
            save_json(DATA_FILE, current_data)
        st.toast("🎉 프로젝트 설계도가 저장되었습니다!")

def render_activity3(user_key):
    category = ACTIVITIES[2]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    st.markdown(INFO_BOX.format("학기를 마무리하며 자신의 성장을 돌아보고 학교생활기록부(세특) 작성을 위한 밑거름 자료를 만듭니다."), unsafe_allow_html=True)
    
    st.markdown("#### 1. 역량별 자기 평가")
    default_df = pd.DataFrame([
        {"평가 항목": "수업 참여도 (발표, 경청 등)", "자기 평가 (상/중/하)": "", "구체적 근거": ""},
        {"평가 항목": "학업 성취도 (개념 이해 및 적용)", "자기 평가 (상/중/하)": "", "구체적 근거": ""},
        {"평가 항목": "문제해결 및 탐구 능력", "자기 평가 (상/중/하)": "", "구체적 근거": ""}
    ])
    df = pd.DataFrame(ans.get("df", default_df.to_dict('records')))
    edited_df = st.data_editor(df, use_container_width=True, hide_index=True)
    
    st.markdown("#### 2. 종합 성찰 및 후속 활동")
    learned = st.text_area("이번 학기 해당 과목에서 가장 기억에 남는 배움과 나의 성장 포인트는 무엇인가요?", value=ans.get("learned", ""))
    next_step = st.text_area("배운 내용을 바탕으로 방학이나 다음 학기에 더 알아보고 싶은 후속 활동(독서, 심화탐구, 관련 진로탐색 등)을 적어보세요.", value=ans.get("next_step", ""))
    
    if st.button("자기평가 저장하기", type="primary"):
        with db_lock:
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][category] = {
                "df": edited_df.to_dict('records'), "learned": learned, "next_step": next_step
            }
            save_json(DATA_FILE, current_data)
        st.toast("🎉 자기평가가 저장되었습니다!")


# --- 메인 공지사항 렌더링 ---
def render_class_overview(current_role, u_info):
    st.header(f"🎯 [{u_info.get('subject', '전체')}] 수업 학습 시스템")
    st.markdown("---")
    
    app_config = load_json(CONFIG_FILE, {})
    materials = app_config.get("materials", [])
    if materials:
        st.subheader("👨‍🏫 수업 공지 및 자료실")
        for mat in materials:
            # 관리자는 모든 자료 보기, 학생은 자기 과목 자료만(또는 전체공지) 보기
            if mat.get("subject", "전체") in ["전체", u_info.get('subject', '')]:
                if mat["type"] == "link": st.markdown(f"🔗 **[{mat['title']}]({mat['content']})**")
                elif mat["type"] == "file" and os.path.exists(mat["content"]):
                    with open(mat["content"], "rb") as f: 
                        st.download_button(f"📥 {mat['title']} ({mat['filename']}) 다운로드", f, file_name=mat['filename'], key=f"mat_dl_{mat['id']}")
        st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        with st.expander("📝 상시 작성 활동지 (클릭 시 이동)", expanded=True):
            st.caption("수업 중 또는 수행평가 진행 시 선생님의 안내에 따라 아래 버튼을 눌러 작성하세요.")
            for act in ACTIVITIES:
                if st.button(f"📄 {act}", use_container_width=True):
                    st.session_state.current_page = act; st.rerun()
    with col2:
        with st.expander("📚 유용한 링크모음", expanded=True):
            st.markdown(f"🔗 [학교 홈페이지 바로가기](#)", unsafe_allow_html=True)
            st.markdown(f"🔗 [수업 질문 게시판 (패들렛 등)](#)", unsafe_allow_html=True)


# --- [4] 메인 화면 설정 및 사이드바 로직 ---
st.set_page_config(page_title="수업 학습 시스템", layout="wide")

# CSS (기존 디자인 유지)
st.markdown("""
<style>
[data-testid="stFormSubmitButton"] button, button[kind="primary"] {
    background-color: #FF4B4B !important; color: white !important; font-size: 20px !important; font-weight: 900 !important;
    padding: 10px !important; border-radius: 8px !important; border: none !important; min-height: 50px !important; width: 100% !important;
}
.stMarkdown p { font-size: 16px !important; color: #222222 !important; font-weight: 600 !important; line-height: 1.6 !important; }
[data-testid="stDataFrame"] { border: 2px solid #333 !important; border-radius: 5px; }
table th { background-color: #f0f2f6 !important; font-size: 16px !important; font-weight: 900 !important; text-align:center !important;}
table td { font-size: 15px !important; font-weight: 600 !important; }
</style>
""", unsafe_allow_html=True)

init_system()

if "logged_in" not in st.session_state: 
    st.session_state.logged_in = False
    st.session_state.user_info = None

if "current_page" not in st.session_state: st.session_state.current_page = "main"

st.sidebar.title("🔒 인증 센터")

if st.session_state.logged_in:
    u_info = st.session_state.user_info
    st.sidebar.markdown(f"""
    <div style='background-color: #e8f4f8; padding: 10px; border-radius: 5px; margin-bottom: 15px; line-height: 1.4;'>
        <div style='font-size: 15px; font-weight: bold; color: #0056b3; margin-bottom: 3px;'>🟢 {u_info['name']} 님 로그인 중</div>
        <div style='font-size: 14px; color: #333; margin-bottom: 2px;'>📘 과목: {u_info.get('subject', '전체')}</div>
        <div style='font-size: 14px; color: #333; margin-bottom: 2px;'>🏫 소속: {u_info.get('class_group', '')}</div>
        <div style='font-size: 14px; color: #333;'>🛡️ 권한: {u_info['role']}</div>
    </div>
    """, unsafe_allow_html=True)
    
    if st.sidebar.button("로그아웃", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.user_info = None
        st.session_state.current_page = "main"
        st.rerun()

else:
    auth_choice = st.sidebar.radio("원하는 작업을 선택하세요", ["회원가입", "로그인"])
    users = load_json(USERS_FILE, {})
    
    if auth_choice == "회원가입":
        st.sidebar.subheader("📝 회원가입")
        reg_subject = st.sidebar.selectbox("과목", SUBJECTS)
        reg_class = st.sidebar.selectbox("반", CLASSES_MAP[reg_subject])
        reg_id = st.sidebar.text_input("학번 입력")
        reg_name = st.sidebar.text_input("이름 입력")
        reg_pw = st.sidebar.text_input("비밀번호", type="password")
        
        if st.sidebar.button("가입 신청", type="primary", use_container_width=True):
            if reg_subject and reg_class and reg_id and reg_name and reg_pw:
                user_key = f"{reg_subject}_{reg_class}_{reg_id}"
                with db_lock:
                    fresh_users = load_json(USERS_FILE, {}) 
                    if user_key in fresh_users:
                        st.sidebar.error("❌ 해당 학번이 이미 가입되어 있습니다.")
                    else:
                        fresh_users[user_key] = {
                            "id": reg_id, "password": reg_pw, "name": reg_name, 
                            "role": "학생", "subject": reg_subject, "class_group": reg_class, "approved": False
                        }
                        save_json(USERS_FILE, fresh_users)
                        st.sidebar.success("🎉 가입 완료! 선생님의 승인을 기다려주세요.")
            else: st.sidebar.warning("⚠️ 모든 빈칸을 빠짐없이 입력해주세요.")
                
    elif auth_choice == "로그인":
        login_type = st.sidebar.radio("계정 유형", ["학생", "교사(관리자)"], horizontal=True)
        if login_type == "학생":
            login_subject = st.sidebar.selectbox("과목", SUBJECTS)
            login_class = st.sidebar.selectbox("반", CLASSES_MAP[login_subject])
            input_id = st.sidebar.text_input("학번")
            input_pw = st.sidebar.text_input("비밀번호", type="password")
            
            if st.sidebar.button("로그인", type="primary", use_container_width=True):
                user_key = f"{login_subject}_{login_class}_{input_id}"
                if user_key in users and users[user_key].get("password") == input_pw:
                    if users[user_key].get("approved", True):
                        st.session_state.logged_in = True
                        st.session_state.user_info = users[user_key]
                        st.session_state.user_info["user_key"] = user_key
                        st.rerun()
                    else: st.sidebar.warning("⏳ 선생님의 가입 승인을 대기 중입니다.")
                else: st.sidebar.error("❌ 과목, 반, 학번 또는 비밀번호가 틀렸습니다.")
        else:
            input_id = st.sidebar.text_input("관리자 ID")
            input_pw = st.sidebar.text_input("비밀번호", type="password")
            if st.sidebar.button("로그인", type="primary", use_container_width=True):
                if input_id in ADMIN_ACCOUNTS and input_pw == ADMIN_ACCOUNTS[input_id]["pw"]:
                    admin_info = ADMIN_ACCOUNTS[input_id]
                    st.session_state.logged_in = True
                    st.session_state.user_info = {
                        "user_key": input_id, "id": input_id, "name": admin_info["name"], 
                        "role": "관리자", "subject": "전체", "class_group": "관리자"
                    }
                    st.rerun()
                else: st.sidebar.error("❌ 관리자 정보가 틀렸습니다.")

st.sidebar.markdown("---")
st.sidebar.markdown("<div style='text-align: center; color: #222; font-size: 15px; font-weight: 900;'>🧑‍💻 만든 이:<br><span style='font-size: 20px; color: #000;'>G.E.M.S</span></div>", unsafe_allow_html=True)

# --- [5] 화면 분기 로직 ---
if not st.session_state.logged_in:
    st.title("🏫 수업 통합 학습 시스템")
    st.info("왼쪽 사이드바를 이용해 로그인해주세요.")

else:
    current_role = st.session_state.user_info["role"]
    current_user_key = st.session_state.user_info["user_key"]
    u_info = st.session_state.user_info
    
    app_config = load_json(CONFIG_FILE, {})
    learning_data = load_json(DATA_FILE, {})

    # 활동지 화면
    if st.session_state.current_page in ACTIVITIES:
        act_name = st.session_state.current_page
        st.title(f"📄 {act_name}")
        st.markdown("---")
        
        if current_role == "학생":
            if act_name == ACTIVITIES[0]: render_activity1(current_user_key)
            elif act_name == ACTIVITIES[1]: render_activity2(current_user_key)
            elif act_name == ACTIVITIES[2]: render_activity3(current_user_key)
        else: st.warning("교사/관리자는 메인 화면의 '제출 자료 조회' 탭을 이용해주세요.")
        
        st.markdown("<br><br>", unsafe_allow_html=True)
        if st.button("⬅️ 메인 화면으로 돌아가기", use_container_width=True):
            st.session_state.current_page = "main"; st.rerun()

    # 메인 화면
    elif st.session_state.current_page == "main":
        if current_role == "학생":
            tabs_list = ["📌 수업 공지 및 메인"] + app_config.get("tabs", [])
            tabs_objects = st.tabs(tabs_list)
            
            with tabs_objects[0]:
                render_class_overview(current_role, u_info)
                
            for index, tab_name in enumerate(app_config.get("tabs", [])):
                with tabs_objects[index + 1]:
                    st.subheader(f"📘 {tab_name} 학습 및 제출")
                    display_pdf(app_config["pdfs"].get(tab_name, f"{tab_name}.pdf"))
                    st.markdown("---")
                    questions = app_config["questions"].get(tab_name, [])
                    st.markdown("<div style='color:#555; margin-bottom:10px;'>아래 질문에 답변을 작성하고 <b>[제출 및 저장하기]</b>를 누르세요.</div>", unsafe_allow_html=True)
                    ans_dict = {}
                    for q in questions:
                        q_id = q["id"]
                        ans_data = learning_data.get(current_user_key, {}).get(tab_name, {}).get(q_id, {})
                        existing_text = ans_data.get("text", "") if isinstance(ans_data, dict) else ans_data
                        st.markdown(f"**{q['label']}**")
                        ans_dict[q_id] = st.text_area("내용 작성", value=existing_text, height=120, key=f"text_{current_user_key}_{tab_name}_{q_id}", label_visibility="collapsed")
                        st.markdown("<br>", unsafe_allow_html=True)
                    
                    if st.button("제출 및 저장하기", key=f"btn_tab_{tab_name}", type="primary"):
                        with db_lock:
                            fresh_data = load_json(DATA_FILE, {}) 
                            if current_user_key not in fresh_data: fresh_data[current_user_key] = {}
                            if tab_name not in fresh_data[current_user_key]: fresh_data[current_user_key][tab_name] = {}
                            for q_id, text_val in ans_dict.items():
                                fresh_data[current_user_key][tab_name][q_id] = {"text": text_val}
                            save_json(DATA_FILE, fresh_data)
                        st.toast(f"💾 {tab_name} 자료가 성공적으로 저장되었습니다!")

        elif current_role == "관리자":
            st.title("🛠️ 관리자(교사) 대시보드")
            menu_tabs = st.tabs(["📌 수업 공지", "👥 회원 관리", "🗂️ 차시(Tab) 및 자료 편집", "📥 학생 제출 자료 조회"])
            
            with menu_tabs[0]:
                render_class_overview(current_role, u_info)

            # --- 회원 관리 ---
            with menu_tabs[1]:
                all_users = load_json(USERS_FILE, {})
                pending_users = {k: v for k, v in all_users.items() if not v.get("approved", True) and v.get("role")=="학생"}
                approved_users = {k: v for k, v in all_users.items() if v.get("approved", True) and v.get("role")=="학생"}
                
                st.subheader("⏳ 가입 승인 대기 목록")
                if pending_users:
                    df_pending = pd.DataFrame([{"과목": v.get("subject", "-"), "반": v.get("class_group", "-"), "학번": v.get("id", "-"), "이름": v.get("name", "-")} for k, v in pending_users.items()])
                    st.dataframe(df_pending, use_container_width=True)
                    if st.button("✅ 대기 중인 모든 학생 일괄 승인", type="primary"):
                        with db_lock:
                            fresh_users = load_json(USERS_FILE, {})
                            for uid in pending_users.keys():
                                if uid in fresh_users: fresh_users[uid]["approved"] = True
                            save_json(USERS_FILE, fresh_users)
                        st.success("일괄 승인 완료!"); st.rerun()
                else: st.info("승인 대기 중인 학생이 없습니다.")
                
                st.markdown("---")
                st.subheader("✅ 기존 승인된 학생 목록 (삭제/비번변경)")
                col1, col2 = st.columns(2)
                with col1:
                    filter_subj = st.selectbox("조회할 과목 선택", ["전체"] + SUBJECTS, key="manage_subj")
                with col2:
                    target_classes = ["전체"] + CLASSES_MAP.get(filter_subj, []) if filter_subj != "전체" else ["전체"] + [c for cl in CLASSES_MAP.values() for c in cl]
                    filter_class = st.selectbox("조회할 반 선택", target_classes, key="manage_class")
                
                filtered_approved = {k: v for k, v in approved_users.items() if (filter_subj == "전체" or v.get("subject") == filter_subj) and (filter_class == "전체" or v.get("class_group") == filter_class)}
                df_users = pd.DataFrame([{"과목": v.get("subject", "-"), "반": v.get("class_group", "-"), "학번": v.get("id", "-"), "이름": v.get("name", "-"), "비밀번호": v.get("password", "-")} for k, v in filtered_approved.items()])
                st.dataframe(df_users, use_container_width=True)
                
                if filtered_approved:
                    del_target = st.selectbox("삭제할 학생 선택", ["선택"] + list(filtered_approved.keys()), format_func=lambda x: x if x=="선택" else f"[{filtered_approved[x].get('class_group')}] {filtered_approved[x].get('name')} ({filtered_approved[x].get('id')})")
                    if del_target != "선택" and st.button("⚠️ 해당 학생 영구 삭제", type="primary"):
                        with db_lock:
                            fresh_users = load_json(USERS_FILE, {})
                            if del_target in fresh_users: del fresh_users[del_target]
                            save_json(USERS_FILE, fresh_users)
                        st.success("삭제 완료"); st.rerun()

            # --- 차시 및 자료 편집 ---
            with menu_tabs[2]:
                st.subheader("👨‍🏫 교사용 자료 업로드 (공지사항용)")
                with st.form("upload_mat"):
                    mat_subj = st.selectbox("대상 과목", ["전체 공지"] + SUBJECTS)
                    mat_title = st.text_input("자료 제목")
                    mat_link = st.text_input("외부 링크 URL (있는 경우)")
                    if st.form_submit_button("등록", type="primary"):
                        if mat_title and mat_link:
                            new_mat = {"id": f"mat_{datetime.datetime.now().strftime('%d%H%M%S')}", "title": mat_title, "type": "link", "content": mat_link, "subject": mat_subj}
                            with db_lock:
                                fresh_config = load_json(CONFIG_FILE, {})
                                if "materials" not in fresh_config: fresh_config["materials"] = []
                                fresh_config["materials"].append(new_mat)
                                save_json(CONFIG_FILE, fresh_config)
                            st.success("등록 완료!"); st.rerun()
                st.info("💡 탭(차시) 동적 생성 및 문항 편집 기능은 기존 캠프용 시스템과 동일하게 작동하도록 설정 파일(config.json)을 통해 관리됩니다.")

            # --- 학생 데이터 조회 및 다운로드 ---
            with menu_tabs[3]:
                col_t, col_b = st.columns([8, 2])
                with col_t: st.subheader("📥 학생 학습 활동 및 제출 자료 조회")
                with col_b: 
                    if st.button("🔄 새로고침", type="primary"): st.rerun()
                
                all_users = load_json(USERS_FILE, {})
                learning_data = load_json(DATA_FILE, {})
                
                c1, c2 = st.columns(2)
                view_subj = c1.selectbox("조회할 과목", SUBJECTS)
                view_class = c2.selectbox("조회할 반", ["전체 보기"] + CLASSES_MAP[view_subj])
                
                student_list = [uid for uid, info in all_users.items() if info.get("role") == "학생" and info.get("subject") == view_subj and (view_class == "전체 보기" or view_class == info.get("class_group"))]
                
                if not student_list: st.info("해당 조건에 가입된 학생이 없습니다.")
                else:
                    view_mode = st.radio("조회 모드", ["👤 특정 학생 집중 분석 (HTML/PDF)", "📅 항목별 전체 현황 (엑셀 CSV)"], horizontal=True)
                    st.markdown("---")
                    
                    if view_mode == "👤 특정 학생 집중 분석 (HTML/PDF)":
                        selected_student = st.selectbox("학생 선택", student_list, format_func=lambda x: f"[{all_users[x].get('class_group')}] {all_users[x].get('name')} ({all_users[x].get('id')})")
                        if selected_student:
                            student_answers = learning_data.get(selected_student, {})
                            u_name = all_users[selected_student].get('name', '학생')
                            
                            # HTML 포트폴리오 생성기 (변경된 3종 활동지에 맞게 작성)
                            html_content = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>{u_name} 포트폴리오</title>
                            <style>
                                body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; line-height: 1.6; color: #333; }}
                                h1 {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 20px; }}
                                h2 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 10px; margin-top: 40px; }}
                                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 20px; }}
                                th, td {{ border: 1px solid #bdc3c7; padding: 10px; text-align: left; }}
                                th {{ background-color: #ecf0f1; text-align: center; }}
                                .content-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #e9ecef; white-space: pre-wrap; }}
                            </style></head><body>
                            <h1>📚 {view_subj} 학습 포트폴리오</h1>
                            <div style="text-align: right; margin-bottom: 30px;"><b>반:</b> {all_users[selected_student].get('class_group', '')} | <b>이름:</b> {u_name}</div>
                            """
                            
                            # 1. 활동지 내역 HTML 변환
                            for act in ACTIVITIES:
                                ans = student_answers.get(act, {})
                                if not ans: continue
                                html_content += f"<h2>▶ {act}</h2>"
                                if act == ACTIVITIES[0]: # 배움노트
                                    html_content += f"<p><b>일자:</b> {ans.get('date','')} | <b>주제:</b> {ans.get('topic','')}</p>"
                                    html_content += "<table><tr><th>핵심 키워드</th><th>내용 요약</th></tr>"
                                    for row in ans.get("df1", []): html_content += f"<tr><td>{row.get('핵심 키워드','')}</td><td>{row.get('내용 요약','')}</td></tr>"
                                    html_content += f"</table><p><b>질문/성찰:</b></p><div class='content-box'>{ans.get('reflection','')}</div>"
                                elif act == ACTIVITIES[1]: # 프로젝트 설계도
                                    html_content += f"<p><b>주제:</b> {ans.get('title','')}</p><p><b>동기:</b> {ans.get('motive','')}</p>"
                                    html_content += "<table><tr><th>단계</th><th>세부 계획</th><th>예상시간</th></tr>"
                                    for row in ans.get("df", []): html_content += f"<tr><td>{row.get('단계','')}</td><td>{row.get('세부 계획 및 역할','')}</td><td>{row.get('예상 소요시간','')}</td></tr>"
                                    html_content += f"</table><p><b>예상 결과물:</b></p><div class='content-box'>{ans.get('outcome','')}</div>"
                                elif act == ACTIVITIES[2]: # 자기평가
                                    html_content += "<table><tr><th>평가 항목</th><th>자기 평가</th><th>구체적 근거</th></tr>"
                                    for row in ans.get("df", []): html_content += f"<tr><td>{row.get('평가 항목','')}</td><td>{row.get('자기 평가 (상/중/하)','')}</td><td>{row.get('구체적 근거','')}</td></tr>"
                                    html_content += f"</table><p><b>배우고 느낀 점:</b></p><div class='content-box'>{ans.get('learned','')}</div>"
                                    html_content += f"<p><b>후속 활동:</b></p><div class='content-box'>{ans.get('next_step','')}</div>"

                            # 2. 차시별 동적 탭 텍스트 박스 내역
                            html_content += "<h2>📝 수업 차시별 제출 자료</h2>"
                            for t_name in app_config.get("tabs", []):
                                for q in app_config["questions"].get(t_name, []):
                                    ans_text = student_answers.get(t_name, {}).get(q["id"], {}).get("text", "")
                                    if ans_text:
                                        html_content += f"<h3>[{t_name}] {q.get('label', '')}</h3><div class='content-box'>{ans_text}</div>"
                            
                            html_content += "</body></html>"
                            st.download_button(label=f"📄 {u_name} 학생 포트폴리오 다운로드 (웹문서)", data=html_content.encode('utf-8-sig'), file_name=f"{u_name}_{view_subj}_포트폴리오.html", mime="text/html", type="primary")
                            st.info("다운로드한 파일을 인터넷 창(크롬 등)으로 연 뒤, **우클릭 -> 인쇄 -> PDF로 저장** 하시면 인쇄용 파일이 만들어집니다.")

                    elif view_mode == "📅 항목별 전체 현황 (엑셀 CSV)":
                        selected_view = st.selectbox("다운로드할 활동/차시 선택", ACTIVITIES + app_config.get("tabs", []))
                        
                        csv_data = []
                        for s_uid in student_list:
                            ans = learning_data.get(s_uid, {}).get(selected_view, {})
                            u_info = all_users[s_uid]
                            u_id = u_info.get('id', '')
                            u_name = u_info.get('name', '')
                            u_class = u_info.get('class_group', '')
                            
                            if selected_view in ACTIVITIES:
                                # 엑셀 출력 포맷은 활동지마다 테이블 형태이므로, 
                                # 가로로 데이터를 펼쳐서(Flatten) 저장하기 좋게 간략화하여 추출합니다.
                                if selected_view == ACTIVITIES[0]: # 배움노트
                                    csv_data.append({"반": u_class, "학번": u_id, "이름": u_name, "일자": ans.get('date', ''), "주제": ans.get('topic', ''), "성찰": ans.get('reflection', '')})
                                elif selected_view == ACTIVITIES[1]: # 설계도
                                    csv_data.append({"반": u_class, "학번": u_id, "이름": u_name, "주제": ans.get('title', ''), "동기": ans.get('motive', ''), "예상결과물": ans.get('outcome', '')})
                                elif selected_view == ACTIVITIES[2]: # 자기평가
                                    csv_data.append({"반": u_class, "학번": u_id, "이름": u_name, "느낀점": ans.get('learned', ''), "후속활동": ans.get('next_step', '')})
                            else: # 차시(Tab) 텍스트 입력들
                                for q in app_config["questions"].get(selected_view, []):
                                    ans_text = learning_data.get(s_uid, {}).get(selected_view, {}).get(q["id"], {}).get("text", "")
                                    csv_data.append({"반": u_class, "학번": u_id, "이름": u_name, "문항": q.get('label', ''), "답변": ans_text})
                        
                        if csv_data:
                            df_csv = pd.DataFrame(csv_data)
                            st.dataframe(df_csv, use_container_width=True)
                            st.download_button(f"📊 {selected_view[:8]}.. 엑셀 다운로드", data=df_csv.to_csv(index=False).encode('utf-8-sig'), file_name=f"{view_subj}_{view_class}_{selected_view[:6]}.csv", mime='text/csv', type="primary")
                        else:
                            st.info("해당 활동지에 제출된 데이터가 없습니다.")