import { useEffect, useState } from 'react'
import AuthPage from '@renderer/pages/AuthPage'
import PermissionsPage from '@renderer/pages/PermissionsPage'
import ShortcutPage from '@renderer/pages/ShortcutPage'

type OnboardingStep = 'auth' | 'permissions' | 'shortcut'

function App(): React.JSX.Element {
  const [step, setStep] = useState<OnboardingStep>('auth')

  useEffect(() => {
    // Signed in but onboarding unfinished (e.g. quit before choosing a
    // shortcut): resume after the sign-in step.
    window.api.hasSession().then((has) => { if (has) setStep('permissions') }).catch(() => {})
    // The window is hidden, not reloaded, between sessions — reset on sign-out.
    return window.api.onSignedOut(() => setStep('auth'))
  }, [])

  function handleOnboardingComplete() {
    window.api.launchOverlay()
    window.api.closeMainWindow()
  }

  return (
    <div style={{ width: '100%', height: '100%' }}>
      {step === 'auth' && <AuthPage onComplete={() => setStep('permissions')} />}
      {step === 'permissions' && <PermissionsPage onComplete={() => setStep('shortcut')} />}
      {step === 'shortcut' && <ShortcutPage onComplete={handleOnboardingComplete} />}
    </div>
  )
}

export default App
