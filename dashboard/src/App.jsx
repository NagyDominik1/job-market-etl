import { useEffect, useState } from 'react'
import { configError, supabase } from './supabase.js'
import Login from './Login.jsx'
import JobFeed from './JobFeed.jsx'

export default function App() {
  // undefined = still checking, null = logged out, object = logged in
  const [session, setSession] = useState(undefined)

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
  return session ? <JobFeed session={session} /> : <Login />
}
