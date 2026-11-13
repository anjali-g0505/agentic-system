ARG BASE_IMAGE=base_image:1.0
FROM ${BASE_IMAGE}
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && pip check
COPY . .
