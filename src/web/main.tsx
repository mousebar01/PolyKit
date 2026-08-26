import React from 'react'
import ReactDOM from 'react-dom/client'
import App from '../App'
import { installServerWorkflowBridge } from './server-workflow-bridge'
import { installWebRuntimeBridge } from './web-runtime'
import '@styles/globals.css'
import '@xyflow/react/dist/style.css'

installWebRuntimeBridge()
installServerWorkflowBridge()

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
