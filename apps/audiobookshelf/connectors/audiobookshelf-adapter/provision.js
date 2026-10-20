// Prints a new Audiobookshelf API key, named "Mu3Lab MCP", for the owner's root account.
// Audiobookshelf signs people in through Authentik only, so Mu3Lab has no password to sign in
// with. Run inside the container, this reads the server's token secret, signs a five-minute
// access token for the root account (the same token shape Audiobookshelf issues), and creates
// the key through Audiobookshelf's own API. An earlier Mu3Lab MCP key is deleted first.
const crypto = require('node:crypto')
const path = require('node:path')
const { DatabaseSync } = require('node:sqlite')

const NAME = 'Mu3Lab MCP'
const BASE = `http://127.0.0.1:${process.env.PORT || '80'}/api`

function encode(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

function sign(payload, secret) {
  const body = `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode(payload)}`
  return `${body}.${crypto.createHmac('sha256', secret).update(body).digest('base64url')}`
}

async function call(token, method, route, body) {
  const response = await fetch(BASE + route, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  })
  if (!response.ok) throw new Error(`Audiobookshelf refused ${method} ${route} (HTTP ${response.status})`)
  const text = await response.text()
  return text ? JSON.parse(text) : null
}

async function main() {
  const database = new DatabaseSync(path.join(process.env.CONFIG_PATH || '/config', 'absdatabase.sqlite'), {
    readOnly: true
  })
  let secret = process.env.JWT_SECRET_KEY
  if (!secret) {
    const row = database.prepare("SELECT value FROM settings WHERE key = 'server-settings'").get()
    secret = row ? JSON.parse(row.value).tokenSecret : ''
  }
  const root = database.prepare("SELECT id, username FROM users WHERE type = 'root' AND isActive = 1").get()
  database.close()
  if (!secret) throw new Error('Audiobookshelf has not finished its first start')
  if (!root) throw new Error('Audiobookshelf has no active owner account')

  const now = Math.floor(Date.now() / 1000)
  const token = sign({ userId: root.id, username: root.username, iat: now, exp: now + 300 }, secret)
  const listed = (await call(token, 'GET', '/api-keys')) || {}
  for (const key of listed.apiKeys || []) {
    if (key.name === NAME && key.userId === root.id) await call(token, 'DELETE', `/api-keys/${key.id}`)
  }
  const created = await call(token, 'POST', '/api-keys', { name: NAME, userId: root.id, isActive: true })
  const apiKey = created && created.apiKey && created.apiKey.apiKey
  if (!apiKey) throw new Error('Audiobookshelf did not return the new API key')
  console.log(`MU3LAB_OUTPUT api_key=${apiKey}`)
}

main().catch((error) => {
  console.log(`MU3LAB_ERROR ${String(error.message || error).replace(/\n/g, ' ')}`)
  process.exit(1)
})
