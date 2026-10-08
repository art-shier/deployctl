import {defineConfig} from '@playwright/test'
export default defineConfig({testDir:'./tests',workers:1,retries:0,use:{baseURL:process.env.CTL_BROWSER_URL||'http://127.0.0.1:8080',headless:true,viewport:{width:1440,height:1000}},reporter:'list',timeout:30000})
