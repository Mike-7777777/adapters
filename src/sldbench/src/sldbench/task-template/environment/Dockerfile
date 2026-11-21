FROM python:3.12-slim

# Set working directory
WORKDIR /app

RUN pip install --no-cache-dir "datasets" "pandas" "scikit-learn" 

# Only the train split goes into the image; the verifier loads the test split itself.
RUN python -c "import datasets; datasets.load_dataset('pkuHaowei/sldbench', {task_name}, split='train').save_to_disk('./data')" \
    && rm -rf /root/.cache/huggingface
