ARG NODE_VERSION=20-alpine

FROM node:${NODE_VERSION} AS base
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM base AS dev
COPY . /app
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM base AS build
COPY . /app
RUN npm run build

FROM nginx:1.27-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html/rabota
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
