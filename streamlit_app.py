import streamlit as st
import requests
import json
import time
from datetime import datetime
import databricks.sql
import threading

# -----------------------------
# Streamlit App Setup
# -----------------------------
st.set_page_config(page_title="Field Staff Chatbot")
st.title("Field Staff Chatbot 3")

# -----------------------------
# Session State Initialization
# -----------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

if "pending_feedback" not in st.session_state:
    st.session_state.pending_feedback = None

# -----------------------------
# Feedback Storage Function
# -----------------------------
def store_feedback(question, answer, score, comment, category):
    """Store thumbs up/down feedback asynchronously in Databricks."""
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

# -----------------------------
# Function to Simulate Token Streaming
# -----------------------------
def simulate_streaming_text(full_text, placeholder, delay=0.02):
    """Render tokens progressively when endpoint doesn't stream."""
    reply = ""
    for token in full_text.split():
        reply += token + " "
        placeholder.markdown(reply + "▌")
        time.sleep(delay)
    placeholder.markdown(reply)
    return reply

# -----------------------------
# Handle User Input + Streaming
# -----------------------------
if user_input := st.chat_input("Ask a question..."):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": user_input})

    # Prepare payload
    payload = {"messages": st.session_state.messages}
    headers = {
        "Authorization": f"Bearer {st.secrets['DATABRICKS_PAT']}",
        "Content-Type": "application/json"
    }

    reply = ""
    try:
        # Send request to Databricks endpoint with streaming enabled
        with requests.post(
            url=st.secrets["ENDPOINT_URL"],
            headers=headers,
            json=payload,
            timeout=300,
            stream=True
        ) as response:
            if response.status_code != 200:
                reply = f"❌ Request failed with {response.status_code}: {response.text}"
            else:
                # Create placeholder for assistant reply
                message_placeholder = st.chat_message("assistant").empty()

                got_streaming = False
                for line in response.iter_lines():
                    if line:
                        try:
                            data = json.loads(line.decode("utf-8"))

                            # Handle OpenAI-style streaming JSON
                            if "choices" in data and len(data["choices"]) > 0:
                                delta = data["choices"][0].get("delta", {})
                                token = delta.get("content", "")
                                if token:
                                    got_streaming = True
                                    reply += token
                                    message_placeholder.markdown(reply + "▌")
                        except json.JSONDecodeError:
                            continue

                # Fallback: if we got no streaming chunks, parse full JSON
                if not got_streaming:
                    try:
                        data = response.json()
                        if "choices" in data and isinstance(data["choices"], list):
                            reply = data["choices"][0]["message"]["content"]
                        elif isinstance(data, str):
                            reply = data
                        else:
                            reply = "⚠️ Unexpected response format."
                    except Exception:
                        reply = response.text or "⚠️ Could not parse model response."

                    # Simulate streaming for better UX
                    reply = simulate_streaming_text(reply, message_placeholder)

                else:
                    # Finalize display for real streaming
                    message_placeholder.markdown(reply)

    except requests.exceptions.RequestException as e:
        reply = f"❌ Connection error: {e}"

    # Save assistant response
    st.session_state.messages.append({"role": "assistant", "content": reply})

# -----------------------------
# Display Chat Messages + Feedback
# -----------------------------
if st.session_state.messages:
    just_submitted_feedback = False

    for idx, msg in enumerate(st.session_state.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

        # Feedback only for assistant messages
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
                    st.session_state.pending_feedback = idx

                if thumbs_down:
                    st.session_state[feedback_key] = "thumbs_down"
                    st.session_state.pending_feedback = idx

            # Handle feedback forms dynamically
            if st.session_state.pending_feedback == idx:
                # Thumbs Down → Ask for category + comment
                if st.session_state.get(feedback_key) == "thumbs_down":
                    with st.form(f"thumbs_down_form_{idx}"):
                        st.subheader("Sorry about that — how can we improve?")
                        feedback_category = st.selectbox(
                            "What type of issue best describes the problem?",
                            ["inaccurate", "outdated", "too long", "too short", "other"],
                            key=f"category_{idx}"
                        )
                        feedback_comment = st.text_area(
                            "What could be better?",
                            key=f"comment_{idx}"
                        )
                        submitted_down = st.form_submit_button("Submit Feedback 👎")

                        if submitted_down:
                            st.session_state.pending_feedback = None
                            st.toast("✅ Your feedback was recorded!")
                            threading.Thread(
                                target=store_feedback,
                                args=(question, msg["content"], "thumbs_down", feedback_comment, feedback_category)
                            ).start()
                            just_submitted_feedback = True

                # Thumbs Up → Optional additional thoughts
                elif st.session_state.get(feedback_key) == "thumbs_up":
                    with st.form(f"thumbs_up_form_{idx}"):
                        feedback_comment = st.text_area(
                            "Please provide any additional thoughts (optional)",
                            key=f"comment_{idx}"
                        )
                        submitted_up = st.form_submit_button("Submit Feedback 👍")

                        if submitted_up:
                            st.session_state.pending_feedback = None
                            st.toast("✅ Thanks for sharing more detail!")
                            threading.Thread(
                                target=store_feedback,
                                args=(question, msg["content"], "thumbs_up", feedback_comment, "")
                            ).start()
                            just_submitted_feedback = True

            # Show success message after feedback submission
            if feedback_status in ["thumbs_up", "thumbs_down"] or just_submitted_feedback:
                st.success("🎉 Thanks for your feedback!")
