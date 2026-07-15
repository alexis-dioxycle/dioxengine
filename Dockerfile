# Stage 1: build the frontend against the panel (node_modules baked in)
FROM dioxycle-app-base:node20 AS frontend-builder
WORKDIR /app
# The panel provides package.json + node_modules. You bring only your source.
COPY frontend/index.html ./
COPY frontend/vite.config.js ./
COPY frontend/src ./src
RUN npm run build

# Stage 2: backend image, with built frontend as static assets
FROM dioxycle-app-base:py311
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=frontend-builder /app/dist ./static
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
