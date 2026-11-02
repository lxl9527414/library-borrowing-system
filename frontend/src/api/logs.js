import request from './request'

export const getOperationLogs = (params) => request.get('/logs', { params })
export const getLogModules = () => request.get('/logs/modules')
