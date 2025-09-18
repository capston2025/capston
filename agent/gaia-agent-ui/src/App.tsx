import { type DragEvent, useMemo, useRef, useState } from 'react'
import './App.css'

type View = 'setup' | 'execution' | 'report'
type Mode = 'plan' | 'instant'
type UploadState = {
  status: 'idle' | 'success' | 'error'
  fileName?: string
  message?: string
}

type FailureReport = {
  id: string
  title: string
  summary: string
  aiAnalysis: string
  screenshotNote: string
  videoNote: string
  technicalLogs: string[]
}

type LogEntry = {
  id: string
  icon: string
  timestamp: string
  text: string
}

const sampleLogs: LogEntry[] = [
  {
    id: 'log-1',
    icon: '🌐',
    timestamp: '14:02:10',
    text: "URL 접근 중... DOM 구조 스캔 시작.",
  },
  {
    id: 'log-2',
    icon: '🔍',
    timestamp: '14:02:12',
    text: "실시간 DOM 분석: input[#user_id], input[#user_pwd] 발견.",
  },
  {
    id: 'log-3',
    icon: '🧠',
    timestamp: '14:02:14',
    text: "Gemini 2.5-flash AI 분석: 로그인 폼 구조 인식 완료.",
  },
  {
    id: 'log-4',
    icon: '⚡️',
    timestamp: '14:02:16',
    text: "자동 테스트 시나리오 생성: TC-001 로그인 기능, TC-002 강좌 접근.",
  },
  {
    id: 'log-5',
    icon: '🚀',
    timestamp: '14:02:18',
    text: "실행 가능한 Playwright 코드 생성 완료. 테스트 시작.",
  },
  {
    id: 'log-6',
    icon: '✅',
    timestamp: '14:02:20',
    text: "셀렉터 input[value='LOGIN'] 클릭 성공.",
  },
]

const failureReports: FailureReport[] = [
  {
    id: 'TC-017',
    title: "TC-017 '매칭 신청 버튼 노출'",
    summary: '매칭 신청 버튼이 기획서 대비 위치가 하단으로 밀려나 있음',
    aiAnalysis: "팝업 레이어가 유지된 상태에서 버튼이 가려져 있어 타겟팅에 실패했습니다. 팝업 close 액션 추가가 필요합니다.",
    screenshotNote: '버튼이 가려진 영역을 붉은색 박스로 표시했습니다.',
    videoNote: '7초 분량 재생 중 3초 지점에서 DOM 검사 장면 확인 가능.',
    technicalLogs: [
      "DOMSnapshotWarning: iframe layer detected, z-index=1200",
      "Console: UnhandledPromiseRejection DOMException: Element is not clickable at point",
      "Network: GET /api/matching/button-text 200 (124ms)",
    ],
  },
  {
    id: 'TC-022',
    title: "TC-022 '비로그인 사용자 접근 제어'",
    summary: '비로그인 사용자에게도 예약 CTA가 노출됨',
    aiAnalysis: '세션 상태 확인 시점이 지연되며, Guard 컴포넌트에서 redirect가 발생하지 않습니다. 조건 분기 로직 점검이 필요합니다.',
    screenshotNote: '예약 CTA가 강조 색상으로 표시된 상태를 캡처.',
    videoNote: '재생 5초 지점에서 브라우저 콘솔 경고 확인.',
    technicalLogs: [
      'Console: Warning - useAuthGuard returned undefined state',
      'Console: Attempted navigation to /reserve blocked by router guard',
      'Network: POST /api/session/check 401 (332ms)',
    ],
  },
]

function App() {
  const [view, setView] = useState<View>('setup')
  const [mode, setMode] = useState<Mode>('instant')
  const [executionMode, setExecutionMode] = useState<Mode>('plan')
  const [testUrl, setTestUrl] = useState('https://cyber.inu.ac.kr')
  const [naturalPrompt, setNaturalPrompt] = useState('')
  const [uploadState, setUploadState] = useState<UploadState>({ status: 'idle' })
  const [isDragging, setIsDragging] = useState(false)
  const [openFailure, setOpenFailure] = useState<string | null>(failureReports[0]?.id ?? null)
  const [realTimeLogs, setRealTimeLogs] = useState<LogEntry[]>([])
  const [isTestRunning, setIsTestRunning] = useState(false)
  const [generatedScenarios, setGeneratedScenarios] = useState<any[]>([])

  const fileInputRef = useRef<HTMLInputElement>(null)

  const totalScenarios = 48
  const passedScenarios = 45
  const failedScenarios = totalScenarios - passedScenarios
  const successRate = Math.round((passedScenarios / totalScenarios) * 100)

  const canStart = useMemo(() => {
    if (!testUrl.trim()) return false
    if (mode === 'instant') {
      // URL 기반 자동 분석은 URL만 있으면 실행 가능
      return true
    }
    // 기획서 모드는 URL만 있어도 실행 가능 (기획서는 선택사항)
    return true
  }, [mode, testUrl])

  const handleFileSelection = (files: FileList | null) => {
    if (!files || files.length === 0) return
    const file = files[0]
    const extension = file.name.split('.').pop()?.toLowerCase()
    const isSupported = extension ? ['pdf', 'docx'].includes(extension) : false

    if (isSupported) {
      setUploadState({
        status: 'success',
        fileName: file.name,
        message: '파일 업로드 완료!',
      })
    } else {
      setUploadState({
        status: 'error',
        fileName: file.name,
        message: '지원하지 않는 파일 형식입니다.',
      })
    }
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragging(false)
    handleFileSelection(event.dataTransfer.files)
  }

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (!isDragging) {
      setIsDragging(true)
    }
  }

  const handleDragLeave = () => {
    setIsDragging(false)
  }

  const addLog = (icon: string, text: string) => {
    const newLog: LogEntry = {
      id: `log-${Date.now()}`,
      icon,
      timestamp: new Date().toLocaleTimeString('ko-KR', { hour12: false }),
      text
    }
    setRealTimeLogs(prev => [...prev, newLog])
  }

  const handleStartTest = async () => {
    if (!canStart) return
    setExecutionMode(mode)
    setView('execution')
    setIsTestRunning(true)
    setRealTimeLogs([])
    
    // 실시간 로그 시뮬레이션
    addLog('🌐', `URL 접근 중: ${testUrl}`)
    
    setTimeout(() => addLog('🔍', 'DOM 구조 스캔 시작...'), 1000)
    
    // 백엔드 API 호출
    try {
      const requestBody = {
        url: testUrl,
        document_content: naturalPrompt || undefined
      }
      
      setTimeout(() => addLog('🧠', 'Gemini 2.5-flash AI 분석 시작...'), 2000)
      
      const response = await fetch('http://localhost:8000/analyze-and-generate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
      })
      
      if (response.ok) {
        const testScenarios = await response.json()
        console.log('Generated test scenarios:', testScenarios)
        setGeneratedScenarios(testScenarios)
        
        setTimeout(() => {
          addLog('⚡️', `테스트 시나리오 ${testScenarios.length}개 생성 완료`)
          addLog('🚀', '실행 가능한 Playwright 코드 생성 완료')
          addLog('✅', '테스트 실행 시작')
          setIsTestRunning(false)
        }, 3000)
      } else {
        console.error('API error:', await response.text())
        setTimeout(() => {
          addLog('❌', 'API 호출 실패 - 대체 시나리오 사용')
          setIsTestRunning(false)
        }, 3000)
      }
    } catch (error) {
      console.error('Failed to call API:', error)
      setTimeout(() => {
        addLog('❌', '연결 실패 - 대체 시나리오 사용')
        setIsTestRunning(false)
      }, 3000)
    }
  }

  const handleViewReport = () => {
    setView('report')
  }

  const donutStyle = useMemo(() => {
    const successAngle = (passedScenarios / totalScenarios) * 360
    return {
      background: `conic-gradient(#4bb07c 0deg ${successAngle}deg, #2b2f36 ${successAngle}deg 360deg)`,
    }
  }, [passedScenarios, totalScenarios])

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">GAIA Agent</div>
        <div className="header-meta">
          <span className="meta-pill">Dark Mode</span>
          <span className="meta-pill">v1.0</span>
        </div>
      </header>

      <main className="view-container">
        {view === 'setup' && (
          <section className="view-card">
            <div className="view-heading">
              <div>
                <h1>테스트 설정</h1>
                <p>GAIA에게 어떤 테스트를 실행할지 설정하세요.</p>
              </div>
              <button
                className="ghost"
                onClick={() => {
                  setUploadState({ status: 'idle' })
                  setNaturalPrompt('')
                  setTestUrl('')
                }}
              >
                초기화
              </button>
            </div>

            <label className="field">
              <span className="field-label">테스트 대상 URL</span>
              <input
                type="url"
                placeholder="https://staging.gemgoo.com"
                value={testUrl}
                onChange={(event) => setTestUrl(event.target.value)}
              />
            </label>

            <div className="mode-tabs">
              <button
                className={mode === 'instant' ? 'active' : ''}
                onClick={() => setMode('instant')}
              >
                🚀 URL 기반 자동 분석 (NEW!)
              </button>
              <button
                className={mode === 'plan' ? 'active' : ''}
                onClick={() => setMode('plan')}
              >
                기획서 기반 테스트 (선택사항)
              </button>
            </div>

            {mode === 'instant' ? (
              <div className="tab-panel">
                <div className="auto-analysis-info">
                  <div className="feature-highlight">
                    <h3>🧠 AI 자동 DOM 분석</h3>
                    <p>URL만 입력하면 GAIA가 웹사이트를 실시간 분석하여 자동으로 테스트 시나리오를 생성합니다.</p>
                    <ul className="feature-list">
                      <li>✅ 실시간 DOM 구조 스캔</li>
                      <li>✅ 정확한 셀렉터 자동 추출</li>
                      <li>✅ AI 기반 테스트 시나리오 생성</li>
                      <li>✅ 기획서 없이도 포괄적 테스트</li>
                    </ul>
                  </div>
                </div>
                <label className="field">
                  <span className="field-label">추가 테스트 요구사항 (선택사항)</span>
                  <textarea
                    rows={4}
                    placeholder="예: 로그인 후 강좌 접근 시나리오 중점 테스트, 결제 프로세스 검증 등... (비워두면 자동으로 모든 기능을 테스트합니다)"
                    value={naturalPrompt}
                    onChange={(event) => setNaturalPrompt(event.target.value)}
                  />
                </label>
              </div>
            ) : (
              <div className="tab-panel">
                <div
                  className={`upload-area ${isDragging ? 'dragging' : ''} ${uploadState.status}`}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.docx"
                    onChange={(event) => handleFileSelection(event.target.files)}
                  />
                  {uploadState.status === 'idle' && (
                    <>
                      <strong>기획서 파일 (선택사항)</strong>
                      <span>PDF 또는 DOCX 파일을 추가하면 더 정확한 테스트를 생성합니다.</span>
                    </>
                  )}
                  {uploadState.status === 'success' && (
                    <>
                      <span className="status-icon">✅</span>
                      <strong>{uploadState.fileName}</strong>
                      <span>{uploadState.message}</span>
                    </>
                  )}
                  {uploadState.status === 'error' && (
                    <>
                      <span className="status-icon">❌</span>
                      <strong>{uploadState.fileName}</strong>
                      <span>{uploadState.message}</span>
                    </>
                  )}
                </div>
                <p className="helper-text">💡 URL만으로도 완전한 테스트가 가능합니다. 기획서는 추가 컨텍스트 제공용입니다.</p>
              </div>
            )}

            <div className="actions">
              <button className="primary" disabled={!canStart} onClick={handleStartTest}>
                테스트 실행
              </button>
            </div>
          </section>
        )}

        {view === 'execution' && (
          <section className="execution-view">
            <div className="execution-header">
              <div>
                <h1>테스트 실행 중</h1>
                <p>
                  {executionMode === 'instant'
                    ? '🚀 URL 기반 자동 DOM 분석 중... AI가 웹사이트 구조를 실시간으로 분석하여 테스트 시나리오를 생성하고 있습니다.'
                    : '기획서 기반 전체 테스트를 실행하고 있습니다.'}
                </p>
              </div>
              <button className="ghost" onClick={handleViewReport}>
                결과 리포트 보기
              </button>
            </div>
            <div className="live-grid">
              <div className="live-preview">
                {testUrl ? (
                  <iframe title="live-preview" src={testUrl} sandbox="allow-same-origin allow-scripts allow-forms" />
                ) : (
                  <div className="preview-placeholder">
                    <strong>실행 중인 화면이 여기에 표시됩니다.</strong>
                    <span>테스트 URL을 입력하면 실시간 미리보기가 활성화됩니다.</span>
                  </div>
                )}
              </div>
              <aside className="live-logs">
                <header>
                  <h2>GAIA 대시보드</h2>
                  <span className="log-subtitle">실시간 행동 로그</span>
                </header>
                <ul>
                  {(realTimeLogs.length > 0 ? realTimeLogs : sampleLogs).map((log) => (
                    <li key={log.id}>
                      <span className="icon" aria-hidden>{log.icon}</span>
                      <span className="timestamp">[{log.timestamp}]</span>
                      <span className="message">{log.text}</span>
                    </li>
                  ))}
                  {isTestRunning && (
                    <li key="loading">
                      <span className="icon" aria-hidden>⏳</span>
                      <span className="timestamp">[처리중]</span>
                      <span className="message">AI가 분석 중...</span>
                    </li>
                  )}
                </ul>
              </aside>
            </div>
            <footer className="execution-footer">
              <div className="status-badge running">Live</div>
              <span>GAIA가 사용자의 시나리오에 따라 테스트를 진행 중입니다.</span>
              <button className="primary" onClick={handleViewReport}>
                테스트 결과 보기
              </button>
            </footer>
          </section>
        )}

        {view === 'report' && (
          <section className="report-view">
            <div className="report-header">
              <div>
                <h1>테스트 결과 리포트</h1>
                <p>실행된 시나리오와 주요 인사이트를 확인하세요.</p>
              </div>
              <button className="ghost" onClick={() => setView('setup')}>
                새 테스트 실행
              </button>
            </div>

            <div className="report-grid">
              <div className="summary-card">
                <div className="donut" style={donutStyle}>
                  <div className="donut-inner">
                    <strong>{successRate}%</strong>
                    <span>성공률</span>
                  </div>
                </div>
                <div className="summary-metrics">
                  <div>
                    <span className="metric-label">총 시나리오</span>
                    <strong>{totalScenarios}</strong>
                  </div>
                  <div>
                    <span className="metric-label success">성공</span>
                    <strong>{passedScenarios}</strong>
                  </div>
                  <div>
                    <span className="metric-label failure">실패</span>
                    <strong>{failedScenarios}</strong>
                  </div>
                </div>
              </div>

              <div className="detail-card">
                <header>
                  <h2>상세 결과 목록</h2>
                  <span className="log-subtitle">실패한 시나리오를 우선 정리했습니다.</span>
                </header>
                <ul>
                  {failureReports.map((report) => {
                    const isOpen = openFailure === report.id
                    return (
                      <li key={report.id} className={isOpen ? 'open' : ''}>
                        <button className="result-header" onClick={() => setOpenFailure(isOpen ? null : report.id)}>
                          <span className="status failure">❌ 실패</span>
                          <div className="result-meta">
                            <strong>{report.id}</strong>
                            <span>{report.title}</span>
                          </div>
                          <span className="summary">{report.summary}</span>
                        </button>
                        {isOpen && (
                          <div className="result-body">
                            <div className="analysis">
                              <h3>AI 원인 분석</h3>
                              <p>{report.aiAnalysis}</p>
                            </div>
                            <div className="evidence">
                              <div>
                                <h3>스크린샷</h3>
                                <div className="screenshot">
                                  <div className="highlight-box" />
                                  <span>{report.screenshotNote}</span>
                                </div>
                              </div>
                              <div>
                                <h3>영상 기록</h3>
                                <div className="video">
                                  <div className="video-bar" />
                                  <span>{report.videoNote}</span>
                                </div>
                              </div>
                            </div>
                            <div className="tech-logs">
                              <h3>기술 로그</h3>
                              <ul>
                                {report.technicalLogs.map((logLine, index) => (
                                  <li key={`${report.id}-log-${index}`}>{logLine}</li>
                                ))}
                              </ul>
                            </div>
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}

export default App
