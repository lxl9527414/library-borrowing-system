import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

request.interceptors.request.use((config) => {
  const token = localStorage.getItem('library_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

request.interceptors.response.use(
  (response) => {
    const result = response.data
    if (result?.code !== 200) {
      ElMessage.error(result?.message || '请求失败')
      return Promise.reject(new Error(result?.message || '请求失败'))
    }
    return result.data
  },
  (error) => {
    const status = error.response?.status
    const message = error.response?.data?.message || error.message || '网络连接失败'
    if (status === 401) {
      localStorage.removeItem('library_token')
      localStorage.removeItem('library_user')
      if (window.location.pathname !== '/login') {
        window.location.href = `/login?redirect=${encodeURIComponent(window.location.pathname)}`
      }
    } else {
      ElMessage.error(message)
    }
    return Promise.reject(error)
  },
)

export default request

