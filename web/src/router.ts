import { createRouter, createWebHashHistory } from 'vue-router';
import FeedbackPage from './pages/FeedbackPage.vue';
import PreferencesPage from './pages/PreferencesPage.vue';
import HomeTab from './tabs/HomeTab.vue';
import MenuTab from './tabs/MenuTab.vue';
import MineTab from './tabs/MineTab.vue';

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/home', component: HomeTab },
    { path: '/menu', component: MenuTab },
    { path: '/mine', component: MineTab },
    { path: '/preferences', component: PreferencesPage },
    { path: '/feedback', component: FeedbackPage },
  ],
});
