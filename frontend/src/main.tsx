import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import SearchPage from './pages/SearchPage.tsx'
import EntityView from './pages/EntityView.tsx'
import FixtureView from './pages/FixtureView.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<SearchPage />} />
          <Route path="team/:id" element={<EntityView entity="team" />} />
          <Route path="player/:id" element={<EntityView entity="player" />} />
          <Route path="fixture/:homeId/vs/:awayId" element={<FixtureView />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
