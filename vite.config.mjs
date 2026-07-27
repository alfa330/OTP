import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

const base = process.env.VITE_BASE_PATH || '/';
const rootDir = process.cwd();

export default defineConfig({
  base,
  resolve: {
    alias: {
      '@': resolve(rootDir, 'src')
    }
  },
  plugins: [react()],
  // keepNames: без него minify стирает имена функций-компонентов, и стек
  // компонентов в окне «Ошибка приложения» состоит из `oe`, `Xt` и т.п. —
  // по фото экрана от оператора такую ошибку не найти.
  esbuild: {
    keepNames: true
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      input: {
        main: resolve(rootDir, 'index.html'),
        callEvaluation: resolve(rootDir, 'call_evaluation.html')
      },
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          vendor: ['axios', 'lodash', 'papaparse', 'jsqr']
        }
      }
    }
  }
});
