import request from './request'

export const getUsers = (params) => request.get('/users', { params })
export const createUser = (data) => request.post('/users', data)
export const updateUser = (id, data) => request.put(`/users/${id}`, data)
export const resetUserPassword = (id, password) => request.put(`/users/${id}/password`, { password })
export const deleteUser = (id) => request.delete(`/users/${id}`)

