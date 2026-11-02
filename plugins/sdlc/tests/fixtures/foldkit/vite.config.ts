import { foldkit } from '@foldkit/vite-plugin'
import { defineConfig } from 'vite'

// The relay only gets a fixed port while serving locally.
export default defineConfig(({ command }) => ({
  plugins: [foldkit({ devToolsMcpPort: command === 'serve' ? 9988 : false })],
}))
