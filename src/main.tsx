import { createRoot } from 'react-dom/client'
import App from './App'
import Admin from './Admin'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  window.location.pathname.startsWith('/admin') ? <Admin /> : <App />,
)
