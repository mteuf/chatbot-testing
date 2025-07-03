import streamlit as st
import requests
from datetime import datetime
import databricks.sql
import threading

st.set_page_config(page_title="Field Staff Chatbot")
st.title("Field Staff Chatbot")

# Initialize chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

if "pending_feedback" not in st.session_state:
    st.session_state.pending_feedback = None

# Function to store feedback in a background thread
def store_feedback(question, answer, score, comment, category):
    try:
        conn = databricks.sql.connect(
            server_hostname=st.secrets["DATABRICKS_SERVER_HOSTNAME"],
            http_path=st.secrets["DATABRICKS_HTTP_PATH"],
            access_token=st.secrets["DATABRICKS_PAT"]
        )
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ai_squad_np.default.feedback
            (question, answer, score, comment, timestamp, category, user)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            question,
            answer,
            score,
            comment,
            datetime.now().isoformat(),
            category,
            ""
        ))
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not store feedback: {e}")

# Handle user input
if user_input := st.chat_input("Ask a question..."):
    st.session_state.messages.append({"role": "user", "content": user_input})

    payload = {"messages": st.session_state.messages}
    headers = {
        "Authorization": f"Bearer {st.secrets['DATABRICKS_PAT']}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(
            url=st.secrets["ENDPOINT_URL"],
            headers=headers,
            json=payload,
            timeout=20
        )
        try:
            result = response.json()
            if "choices" in result and isinstance(result["choices"], list):
                reply = result["choices"][0]["message"]["content"]
            elif isinstance(result, str) and result.strip():
                reply = result
            elif not result or result == "null":
                reply = "⚠️ Model returned no content."
            else:
                reply = f"⚠️ Unexpected format: {result}"
        except Exception:
            reply = response.text or "⚠️ Could not parse model response."
    except requests.exceptions.RequestException as e:
        reply = f"❌ Connection error: {e}"

    st.session_state.messages.append({"role": "assistant", "content": reply})

# Only process if there are messages
if st.session_state.messages:
    just_submitted_feedback = False

    for idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

        if msg["role"] == "assistant":
            question_idx = idx - 1
            question = (
                st.session_state.messages[question_idx]["content"]
                if question_idx >= 0 and st.session_state.messages[question_idx]["role"] == "user"
                else ""
            )

            feedback_key = f"feedback_{idx}"
            feedback_status = st.session_state.get(feedback_key, "none")

            if feedback_status == "none":
                st.write("Was this answer helpful?")
                col1, col2 = st.columns(2)
                thumbs_up = col1.button("👍 Yes", key=f"thumbs_up_{idx}")
                thumbs_down = col2.button("👎 No", key=f"thumbs_down_{idx}")

                if thumbs_up:
                    st.session_state[feedback_key] = "thumbs_up"
                    st.session_state.pending_feedback = idx  # trigger follow-up comment form
                    just_submitted_feedback = True
                    st.toast("✅ Your positive feedback was recorded!")
                    threading.Thread(
                        target=store_feedback,
                        args=(question, msg["content"], "thumbs_up", "", "")
                    ).start()

                if thumbs_down:
                    st.session_state[feedback_key] = "thumbs_down"
                    st.session_state.pending_feedback = idx  # trigger follow-up comment form

            if st.session_state.pending_feedback == idx:
                # thumbs down needs category, thumbs up does not
                if st.session_state.get(feedback_key) == "thumbs_down":
                    with st.form(f"thumbs_down_form_{idx}"):
                        st.subheader("Sorry about that — how can we improve?")
                        feedback_category = st.selectbox(
                            "What type of issue best describes the problem?",
                            ["inaccurate", "outdated", "too long", "too short", "other"],
                            key=f"category_{idx}"
                        )
                        feedback_comment = st.text_area("What could be better?", key=f"comment_{idx}")
                        submitted_down = st.form_submit_button("Submit Feedback 👎")

                        if submitted_down:
                            st.session_state.pending_feedback = None
                            st.toast("✅ Your feedback was recorded!")
                            threading.Thread(
                                target=store_feedback,
                                args=(question, msg["content"], "thumbs_down", feedback_comment, feedback_category)
                            ).start()
                            just_submitted_feedback = True
                elif st.session_state.get(feedback_key) == "thumbs_up":
                    with st.form(f"thumbs_up_form_{idx}"):
                        st.subheader("Thanks for the thumbs up! Any extra comments?")
                        feedback_comment = st.text_area("Any additional thoughts?", key=f"comment_{idx}")
                        submitted_up = st.form_submit_button("Submit Feedback 👍")

                        if submitted_up:
                            st.session_state.pending_feedback = None
                            st.toast("✅ Thanks for sharing more detail!")
                            threading.Thread(
                                target=store_feedback,
                                args=(question, msg["content"], "thumbs_up", feedback_comment, "")
                            ).start()
                            just_submitted_feedback = True

            if feedback_status in ["thumbs_up", "thumbs_down"] or just_submitted_feedback:
                st.success("🎉 Thanks for your feedback!")
