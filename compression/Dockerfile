FROM --platform=linux/arm64 public.ecr.aws/lambda/python:3.12

# Copy function code
COPY app.py ${LAMBDA_TASK_ROOT}

RUN pip3 install boto3 aws_lambda_powertools pillow

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
CMD [ "app.handler" ]