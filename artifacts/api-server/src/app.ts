import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import http from "node:http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();
const dashboardPort = Number(process.env["DASHBOARD_PORT"] ?? "8099");

function proxyDashboard(
  req: express.Request,
  res: express.Response,
): void {
  const proxyRequest = http.request(
    {
      hostname: "127.0.0.1",
      port: dashboardPort,
      method: req.method,
      path: req.url || "/",
      headers: {
        ...req.headers,
        host: `127.0.0.1:${dashboardPort}`,
        "x-forwarded-host":
          req.headers["x-forwarded-host"] ?? req.headers.host ?? "",
        "x-forwarded-proto":
          req.headers["x-forwarded-proto"] ?? req.protocol ?? "https",
      },
    },
    (proxyResponse) => {
      res.status(proxyResponse.statusCode ?? 502);
      for (const [header, value] of Object.entries(proxyResponse.headers)) {
        if (value !== undefined) {
          res.setHeader(header, value);
        }
      }
      proxyResponse.pipe(res);
    },
  );

  proxyRequest.on("error", (error) => {
    logger.error({ error }, "Dashboard proxy request failed");
    if (!res.headersSent) {
      res.status(502).type("text").send("Dashboard service is unavailable.");
    } else {
      res.end();
    }
  });

  req.pipe(proxyRequest);
}

function proxyPublicLeaderboard(
  req: express.Request,
  res: express.Response,
): void {
  const suffix = req.url || "/";
  req.url = `/lb${suffix.startsWith("/") ? suffix : `/${suffix}`}`;
  proxyDashboard(req, res);
}

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());

// The public Replit domain is served by this API service. Keep the bot's
// aiohttp dashboard on its own port, but expose it through the same domain.
app.get("/", (_req, res) => {
  res.redirect(302, "/dashboard/");
});
app.use("/dashboard", proxyDashboard);
app.get("/api", (_req, res) => {
  res.redirect(302, "/api/dashboard/");
});
app.use("/api/dashboard", proxyDashboard);
// Keep the public board available both at the domain root and under the API
// artifact's public /api mount. The Python app owns slug resolution and data.
app.use("/lb", proxyPublicLeaderboard);
app.use("/api/lb", proxyPublicLeaderboard);

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

export default app;
