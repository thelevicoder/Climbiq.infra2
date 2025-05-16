FROM public.ecr.aws/lambda/python:3.9
RUN pip install --no-cache-dir opencv-python-headless numpy -t /opt/python
COPY grade_hold_lambda.py ${LAMBDA_TASK_ROOT}/grade_hold_lambda.py
CMD ["grade_hold_lambda.lambda_handler"]
