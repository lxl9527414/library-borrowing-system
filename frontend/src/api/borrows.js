import request from './request'

export const getBorrows = (params) => request.get('/borrows', { params })
export const borrowBook = (bookId) => request.post('/borrows', { bookId })
export const returnBook = (id) => request.put(`/borrows/${id}/return`)
export const renewBorrow = (id) => request.put(`/borrows/${id}/renew`)
export const settleFine = (id) => request.put(`/borrows/${id}/fine/settle`)
