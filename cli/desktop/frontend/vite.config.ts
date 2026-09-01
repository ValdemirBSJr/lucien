import {defineConfig} from 'vite'
import {svelte} from '@sveltejs/vite-plugin-svelte'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [svelte()],
  build: {
    // Preserva frontend/dist/.gitkeep entre builds -- ele e o unico arquivo
    // rastreado no git ali, para que `//go:embed all:frontend/dist` em
    // main.go resolva mesmo num clone limpo que nunca rodou este build.
    emptyOutDir: false
  }
})
