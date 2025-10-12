FROM node:20-alpine

# Set working directory
WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy source code
COPY tsconfig.json ./
COPY src ./src

# Build TypeScript
RUN npm install -g typescript && tsc

# Expose port
EXPOSE 3000

# Set environment to production
ENV NODE_ENV=production

# Start server
CMD ["node", "dist/server.js"]
