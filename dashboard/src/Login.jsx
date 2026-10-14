import { useState } from 'react'
import { supabase } from './supabase.js'

export default function Login() {
  const [email, setEmail] = useState('')
  const [state, setState] = useState('idle') // idle | sending | sent | error
  const [message, setMessage] = useState('')

  async function handleSubmit(event) {
    event.preventDefault()
    setState('sending')
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: {
        // Never create a new account from this form: only users that already
        // exist in Supabase (me) can log in. Sign-ups are disabled there too.
        shouldCreateUser: false,
        // Where the magic link sends me back: this site (local or Vercel).
        emailRedirectTo: window.location.origin,
      },
    })
    if (error) {
      setState('error')
      // Don't reveal whether an address has an account.
      setMessage('Could not send the link. Check the address, or try again in a minute.')
    } else {
      setState('sent')
    }
  }

  return (
    <main className="login">
      <h1>Job feed</h1>
      {state === 'sent' ? (
        <p className="status">Check your inbox: the login link was sent to {email}.</p>
      ) : (
        <form onSubmit={handleSubmit}>
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <button type="submit" disabled={state === 'sending'}>
            {state === 'sending' ? 'Sending…' : 'Send login link'}
          </button>
          {state === 'error' && <p className="status error">{message}</p>}
        </form>
      )}
    </main>
  )
}
