import request from './request'

export const getBorrowRule = () => request.get('/rules')
export const updateBorrowRule = (data) => request.put('/rules', data)
