port: 8000
listen: true

basicAuthMode: true
basicAuthUser:
  username: "{{USERNAME}}"
  password: "{{PASSWORD}}"

whitelistMode: false
enableUserAccounts: false
allowKeysExposure: false
enableServerPlugins: false

requestOverrides:
  - hosts:
      - "{{API_HOST}}"
    key: "{{MASTER_API_KEY}}"
