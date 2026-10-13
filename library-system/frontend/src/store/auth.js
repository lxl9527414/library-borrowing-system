import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

const TOKEN_KEY = 'library_token'
const USER_KEY = 'library_user'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const user = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'))

  const isAdmin = computed(() => user.value?.role === 'ADMIN')
  const isReader = computed(() => user.value?.role === 'READER')

  function setSession(nextToken, nextUser) {
    token.value = nextToken
    user.value = nextUser
    localStorage.setItem(TOKEN_KEY, nextToken)
    localStorage.setItem(USER_KEY, JSON.stringify(nextUser))
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  }

  return { token, user, isAdmin, isReader, setSession, logout }
})

