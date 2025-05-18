# 1. Use AWS’s official Lambda base image for Python 3.9
FROM public.ecr.aws/lambda/python:3.9

# 2. Install OpenCV and NumPy into /opt/python
RUN pip install --no-cache-dir opencv-python-headless numpy -t /opt/python
RUN pip install requests_toolbelt

 

# 3. Copy all three handler scripts into the image
#    (assumes your handlers live in ../lib/handlers/)
COPY ../lib/handlers/contour_lambda.py    ${LAMBDA_TASK_ROOT}/contour_lambda.py
COPY ../lib/handlers/grade_hold_lambda.py  ${LAMBDA_TASK_ROOT}/grade_hold_lambda.py
COPY ../lib/handlers/grade_route_lambda.py ${LAMBDA_TASK_ROOT}/grade_route_lambda.py

# 4. Default CMD just needs a placeholder; 
#    we'll override Function-specific commands in CDK
CMD ["contour_lambda.lambda_handler"]
