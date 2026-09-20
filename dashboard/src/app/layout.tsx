"use client";
import { Component, type ReactNode } from "react";
import "./globals.css";
export default class RootLayout extends Component<{
  children: ReactNode;
  params: Promise<Record<string, never>>;
}> {
  render() {
    return (
      <html lang="en">
        <head>
          <title>Emporos · Trading terminal</title>
          <meta
            name="description"
            content="Private Emporos trading dashboard"
          />
          <meta name="color-scheme" content="dark" />
        </head>
        <body>{this.props.children}</body>
      </html>
    );
  }
}
