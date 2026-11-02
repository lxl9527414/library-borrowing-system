import request from './request'

export const getReservations = (params) => request.get('/reservations', { params })
export const getActiveReservations = () => request.get('/reservations/active')
export const reserveBook = (bookId) => request.post('/reservations', { bookId })
export const cancelReservation = (id) => request.delete(`/reservations/${id}`)
