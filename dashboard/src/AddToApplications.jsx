import { useState } from 'react'
import { STATUS_LABELS, createApplication, describeError, findDuplicates } from './applications.js'
import './tracker.css'

// "Hozzáadom" on a Job feed card or a Keresés result. `fields` = the known
// applications columns (company_name, position, city, posting_url...).
// Flow: check for an application at the same company first; if there is one,
// show it and ask again instead of silently adding a duplicate.
export default function AddToApplications({ fields }) {
  const [state, setState] = useState('idle') // idle | busy | confirm | added | error
  const [duplicates, setDuplicates] = useState([])
  const [message, setMessage] = useState('')

  async function save() {
    setState('busy')
    try {
      await createApplication(fields)
      setState('added')
    } catch (error) {
      setMessage(describeError(error))
      setState('error')
    }
  }

  async function check() {
    setState('busy')
    try {
      const found = await findDuplicates(fields.company_name)
      if (found.length) {
        setDuplicates(found)
        setState('confirm')
      } else {
        await save()
      }
    } catch (error) {
      setMessage(describeError(error))
      setState('error')
    }
  }

  if (state === 'added') return <span className="add-done">Hozzáadva a jelentkezéseidhez</span>
  if (state === 'confirm') {
    return (
      <div className="duplicate-warning">
        <p>
          Már van jelentkezésed ennél a cégnél:{' '}
          {duplicates
            .map((d) => `${d.company_name}${d.position ? ` – ${d.position}` : ''} (${STATUS_LABELS[d.status]})`)
            .join('; ')}
        </p>
        <button onClick={save}>Mégis hozzáadom</button>
        <button className="secondary" onClick={() => setState('idle')}>Mégse</button>
      </div>
    )
  }
  return (
    <span className="add-action">
      <button className="secondary" onClick={check} disabled={state === 'busy'}>
        {state === 'busy' ? 'Mentés…' : 'Hozzáadom'}
      </button>
      {state === 'error' && <span className="status error">{message}</span>}
    </span>
  )
}
