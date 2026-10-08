FROM python:3.11-slim
WORKDIR /workspace
# The task stores only the record id; fetch this record's conversation from the
# pinned dataset revision (sha256-checked) at build time.
COPY hbp_data.py /tmp/hbp_data.py
RUN python3 /tmp/hbp_data.py {task_id} /workspace/conversation.md && rm /tmp/hbp_data.py
CMD ["/bin/bash"]
