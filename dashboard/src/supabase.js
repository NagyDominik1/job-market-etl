import { createClient } from '@supabase/supabase-js'

// Vite exposes only variables starting with VITE_ to the browser code.
// They come from .env.local locally and from the Vercel project settings.
const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

// The anon key is public by design (every visitor's browser gets it).
// What protects the data is Row Level Security in the database: without a
// login the "anon" role sees nothing; after login the "authenticated" role
// may only SELECT from job_feed (migration 005).
export const configError =
  !url || !anonKey
    ? 'VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY are not set (see dashboard/.env.example).'
    : null

export const supabase = configError ? null : createClient(url, anonKey)
