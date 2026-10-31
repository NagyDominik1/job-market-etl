import { useEffect, useState } from 'react'
import { configError, supabase } from './supabase.js'
import Login from './Login.jsx'
import JobFeed from './JobFeed.jsx'
import SearchTab from './SearchTab.jsx'
import TrackerTab from './TrackerTab.jsx'
import TemplatesTab from './TemplatesTab.jsx'

export default function App() {
  // undefined = still checking, null = logged out, object = logged in
  const [session, setSession] = useState(undefined)
  const [tab, setTab] = useState('feed') // 'feed' | 'search' | 'tracker' | 'templates'

  useEffect(() => {
    if (configError) return
    // The session is kept in localStorage; after clicking the magic link,
    // supabase-js reads the login from the URL by itself.
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data } = supabase.auth.onAuthStateChange((_event, newSession) => setSession(newSession))
    return () => data.subscription.unsubscribe()
  }, [])

  if (configError) return <p className="status error">{configError}</p>
  if (session === undefined) return <p className="status">Loading…</p>
  if (!session) return <Login />
  return (
    <>
      <nav className="tabs" aria-label="Tabs">
        <button className={tab === 'feed' ? '' : 'secondary'} onClick={() => setTab('feed')}>
          Job feed
        </button>
        <button className={tab === 'search' ? '' : 'secondary'} onClick={() => setTab('search')}>
          Keresés
        </button>
        <button className={tab === 'tracker' ? '' : 'secondary'} onClick={() => setTab('tracker')}>
          Jelentkezéseim
        </button>
        <button className={tab === 'templates' ? '' : 'secondary'} onClick={() => setTab('templates')}>
          Sablonok
        </button>
      </nav>
      {tab === 'feed' && <JobFeed session={session} />}
      {tab === 'search' && <SearchTab session={session} />}
      {tab === 'tracker' && <TrackerTab session={session} />}
      {tab === 'templates' && <TemplatesTab session={session} />}
    </>
  )
}
