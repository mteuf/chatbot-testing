import streamlit as st
import requests, json
from datetime import datetime
import databricks.sql
import threading

st.set_page_config(page_title="Field Staff Chatbot")
st.title("Field Staff Chatbot 5")

# -----------------------------
# Session state
# -----------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_feedback" not in st.session_state:
    st.session_state.pending_feedback = None

# -----------------------------
# Feedback writer (background)
# -----------------------------
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

# -----------------------------
# Streaming helper
# -----------------------------
def stream_databricks_chat(messages):
    """
    Yields text chunks from a Databricks chat endpoint that supports SSE ('data:' lines).
    Compatible with OpenAI-style /v1/chat/completions stream responses.
    """
    url = st.secrets["ENDPOINT_URL"]
    headers = {
        "Authorization": f"Bearer {st.secrets['DATABRICKS_PAT']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Connection": "keep-alive",
    }
    payload = {"messages": messages, "stream": True}

    try:
        with requests.post(url, headers=headers, json=payload, stream=True, timeout=300) as r:
            r.raise_for_status()
            for raw_line in r.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                data = raw_line[len("data: "):].strip() if raw_line.startswith("data: ") else raw_line.strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                try:
                    delta = obj["choices"][0].get("delta") or obj["choices"][0].get("message") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        yield piece
                except Exception:
                    piece = obj.get("response") or obj.get("text") or ""
                    if piece:
                        yield piece
    except requests.exceptions.RequestException as e:
        yield f"\n\n❌ Connection error while streaming: {e}"

# -----------------------------
# Feedback renderers
# -----------------------------
def render_feedback_inline(idx: int):
    """Show feedback UI ONLY (no message content); call this right after streaming."""
    msg = st.session_state.messages[idx]
    if msg["role"] != "assistant":
        return

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

    if st.session_state.pending_feedback == idx:
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
                        args=(question, msg["content"], "thumbs_down", feedback_comment, feedback_category),
                        daemon=True
                    ).start()
                    st.success("🎉 Thanks for your feedback!")

        elif st.session_state.get(feedback_key) == "thumbs_up":
            with st.form(f"thumbs_up_form_{idx}"):
                feedback_comment = st.text_area("Please provide any additional thoughts (optional)", key=f"comment_{idx}")
                submitted_up = st.form_submit_button("Submit Feedback 👍")
                if submitted_up:
                    st.session_state.pending_feedback = None
                    st.toast("✅ Thanks for sharing more detail!")
                    threading.Thread(
                        target=store_feedback,
                        args=(question, msg["content"], "thumbs_up", feedback_comment, ""),
                        daemon=True
                    ).start()
                    st.success("🎉 Thanks for your feedback!")

def render_message_with_feedback(idx: int):
    """Render a message and, if assistant, its feedback UI (used for history only)."""
    msg = st.session_state.messages[idx]
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            # Reuse inline renderer inside the same bubble
            render_feedback_inline(idx)

# -----------------------------
# Input
# -----------------------------
user_input = st.chat_input("Ask a question...")

# If new input, append it to history immediately so it renders below
if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})

# -----------------------------
# Render prior history (everything except a *pending* last user)
# -----------------------------
pending_user = None
messages_to_render = st.session_state.messages

if messages_to_render and messages_to_render[-1]["role"] == "user" and user_input:
    pending_user = messages_to_render[-1]
    messages_to_render = messages_to_render[:-1]

for i in range(len(messages_to_render)):
    render_message_with_feedback(i)

# Show the pending user message at the bottom
if pending_user:
    with st.chat_message("user"):
        st.markdown(pending_user["content"])

# -----------------------------
# Stream assistant at the bottom (no duplicate re-render)
# -----------------------------
if pending_user:
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_reply = []
        for token in stream_databricks_chat(st.session_state.messages):
            full_reply.append(token)
            placeholder.markdown("".join(full_reply))
        reply_text = "".join(full_reply).strip() or "⚠️ Model returned no content."
        placeholder.markdown(reply_text)

        # Append to history *before* drawing feedback so keys/indices are stable
        st.session_state.messages.append({"role": "assistant", "content": reply_text})
        new_idx = len(st.session_state.messages) - 1

        # Draw ONLY feedback controls here to avoid duplicating the content
        render_feedback_inline(new_idx)
