FROM public.ecr.aws/lambda/python:3.9
RUN pip install --no-cache-dir opencv-python-headless numpy -t /opt/python
COPY grade_route_lambda.py ${LAMBDA_TASK_ROOT}/grade_route_lambda.py
CMD ["grade_route_lambda.lambda_handler"]