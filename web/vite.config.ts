import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Em desenvolvimento a tela roda aqui e a API noutra porta. O proxy faz o
// navegador ver tudo na mesma origem, o que evita CORS no caminho comum e
// mantém o código do front escrevendo "/api/..." nos dois modos — em
// produção é o próprio FastAPI que serve estes arquivos.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
