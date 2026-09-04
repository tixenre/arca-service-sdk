import { defineConfig } from 'vitest/config'

// Ancla la corrida a ESTE paquete. Sin un config acá, vitest sube hasta la raíz del repo y
// se lleva puestos los tests de `arca_service_ui`, que necesitan React y jsdom.
export default defineConfig({
  root: __dirname,
  test: {
    include: ['test/**/*.test.ts'],
    environment: 'node',
  },
})
