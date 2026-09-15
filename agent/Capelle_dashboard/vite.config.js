import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    port: 5173,
    open: true,
    proxy: {
      '/api': 'http://localhost:5000',
      '/data': 'http://localhost:5000'
    }
  }
});
