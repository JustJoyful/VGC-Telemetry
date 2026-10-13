FROM node:22.22.2-bookworm-slim AS web-assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY scripts/build-web-assets.mjs scripts/build-web-assets.mjs
COPY app/web/ app/web/
RUN npm run build

# =============================================================
# Stage 1: Public API Lambda (Debian slim + Lambda Web Adapter)
# =============================================================
FROM python:3.12-slim AS api

# Install AWS Lambda Web Adapter extension
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter

ENV PORT=8080
ENV AWS_LWA_PORT=8080
ENV PYTHONPATH=/var/task

WORKDIR /var/task

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ /var/task/core/
COPY db/ /var/task/db/
COPY app/ /var/task/app/
COPY ingestion/ /var/task/ingestion/
COPY --from=web-assets /build/app/web/static/ /var/task/app/web/static/
RUN rm -r /var/task/app/web/static/src

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]


# =============================================================
# Stage 2: Ingestion ETL Lambda (ECR Lambda Python 3.12 with RIC)
# =============================================================
FROM public.ecr.aws/lambda/python:3.12 AS ingestion

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ${LAMBDA_TASK_ROOT}/core/
COPY db/ ${LAMBDA_TASK_ROOT}/db/
COPY ingestion/ ${LAMBDA_TASK_ROOT}/ingestion/
COPY app/ ${LAMBDA_TASK_ROOT}/app/

CMD ["ingestion.handler.lambda_handler"]


# =============================================================
# Stage 3: Scraper Lambda (ECR Lambda Python 3.12 with RIC)
# =============================================================
FROM public.ecr.aws/lambda/python:3.12 AS scraper

WORKDIR ${LAMBDA_TASK_ROOT}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ ${LAMBDA_TASK_ROOT}/core/
COPY scraper/ ${LAMBDA_TASK_ROOT}/scraper/
COPY db/ ${LAMBDA_TASK_ROOT}/db/
COPY ingestion/ ${LAMBDA_TASK_ROOT}/ingestion/

CMD ["scraper.handler.lambda_handler"]
