FROM public.ecr.aws/lambda/python:3.9
RUN pip install --no-cache-dir opencv-python-headless numpy -t /opt/python
RUN pip install requests_toolbelt

COPY contour_lambda.py ${LAMBDA_TASK_ROOT}/contour_lambda.py
CMD ["contour_lambda.lambda_handler"]
