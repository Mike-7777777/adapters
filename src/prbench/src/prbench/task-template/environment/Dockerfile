FROM python:3.11-slim
WORKDIR /workspace
# The judge client used by tests/grader.py; installed before the per-task file
# so that every task shares this layer.
RUN pip install --no-cache-dir openai==3.49.0
COPY conversation.md /workspace/conversation.md
CMD ["/bin/bash"]
