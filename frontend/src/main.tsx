import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import SearchPage from './pages/SearchPage.tsx'
import EntityView from './pages/EntityView.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<SearchPage />} />
          <Route path="team/:id" element={<EntityView entity="team" />} />
          <Route path="player/:id" element={<EntityView entity="player" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
