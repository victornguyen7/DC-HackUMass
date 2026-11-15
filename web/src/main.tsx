import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, Navigate, Outlet, RouterProvider } from "react-router-dom";
import { Sidebar } from "./components/Shell";
import Hall from "./pages/Hall";
import Dashboard from "./pages/Dashboard";
import Waste from "./pages/Waste";
import Lab from "./pages/Lab";
import SimilarNights from "./pages/SimilarNights";
import Sam from "./pages/Sam";
import "./styles.css";
import "./guided.css";
import "./similar-nights.css";
import "./sam.css";
import "./tablet.css";

function Layout() {
  return (
    <div className="app">
      <Sidebar />
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/hall" replace /> },
      { path: "hall", element: <Hall /> },
      { path: "dashboard", element: <Dashboard /> },
      { path: "waste", element: <Waste /> },
      { path: "lab", element: <Lab /> },
      { path: "similar-nights", element: <SimilarNights /> },
      { path: "sam", element: <Sam /> },
      { path: "*", element: <Navigate to="/hall" replace /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
