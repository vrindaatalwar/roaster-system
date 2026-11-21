FROM python:3.12-slim AS api
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY backend ./backend
ENV PYTHONPATH=/app/backend
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]

FROM node:22-alpine AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
ARG VITE_API_BASE_URL=
ARG VITE_CLERK_PUBLISHABLE_KEY=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY}
RUN npm run build

FROM nginx:alpine AS web
COPY --from=web-build /web/dist /usr/share/nginx/html
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
ENV API_UPSTREAM=http://backend:8000 \
    NGINX_ENVSUBST_FILTER=API_UPSTREAM
EXPOSE 80
