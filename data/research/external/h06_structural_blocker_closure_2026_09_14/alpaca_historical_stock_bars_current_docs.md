---
updatedAt: 2026-05-27T17:58:03.000Z
---

Fetch the complete documentation index at: https://docs.alpaca.markets/us/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Historical bars (single symbol)

The historical stock bars API provides aggregates for the stock symbol between the specified dates.

# OpenAPI definition

```json
{
  "components": {
    "headers": {
      "ratelimit_limit": {
        "description": "Request limit per minute.",
        "example": 100,
        "schema": {
          "type": "integer"
        }
      },
      "ratelimit_remaining": {
        "description": "Request limit per minute remaining.",
        "example": 90,
        "schema": {
          "type": "integer"
        }
      },
      "ratelimit_reset": {
        "description": "The UNIX epoch when the remaining quota changes.",
        "example": 1674044551,
        "schema": {
          "type": "integer"
        }
      }
    },
    "parameters": {
      "end": {
        "description": "The inclusive end of the interval. Format: RFC-3339 or YYYY-MM-DD.\nDefault: the current time if the user has a real-time access for the feed, otherwise 15 minutes before the current time.\n",
        "examples": {
          "RFC-3339 nanosecond": {
            "summary": "RFC-3339 date-time with nanosecond accuracy",
            "value": "2024-01-04T01:02:03.123456789Z"
          },
          "RFC-3339 second": {
            "summary": "RFC-3339 date-time with second accuracy",
            "value": "2024-01-04T00:00:00Z"
          },
          "RFC-3339 with timezone": {
            "summary": "RFC-3339 date-time with time zone",
            "value": "2024-01-04T09:30:00-04:00"
          },
          "date": {
            "summary": "Date",
            "value": "2024-01-04"
          }
        },
        "in": "query",
        "name": "end",
        "required": false,
        "schema": {
          "format": "date-time",
          "type": "string"
        }
      },
      "limit": {
        "description": "The maximum number of data points to return in the response page.\nThe API may return less, even if there are more available data points in the requested interval.\nAlways check the `next_page_token` for more pages.\nThe limit applies to the total number of data points, not per symbol!\n",
        "in": "query",
        "name": "limit",
        "required": false,
        "schema": {
          "default": 1000,
          "maximum": 10000,
          "minimum": 1,
          "type": "integer"
        }
      },
      "page_token": {
        "description": "The pagination token from which to continue. The value to pass here is returned in specific requests when more data is available, usually because of a response result limit.\n",
        "in": "query",
        "name": "page_token",
        "schema": {
          "type": "string"
        }
      },
      "sort": {
        "description": "Sort data in ascending or descending order.",
        "in": "query",
        "name": "sort",
        "schema": {
          "allOf": [
            {
              "type": "string"
            },
            {
              "$ref": "#/components/schemas/sort"
            }
          ]
        }
      },
      "start": {
        "description": "The inclusive start of the interval. Format: RFC-3339 or YYYY-MM-DD.\nDefault: the beginning of the current day, but at least 15 minutes ago if the user doesn't have real-time access for the feed.\n",
        "examples": {
          "RFC-3339 nanosecond": {
            "summary": "RFC-3339 date-time with nanosecond accuracy",
            "value": "2024-01-03T01:02:03.123456789Z"
          },
          "RFC-3339 second": {
            "summary": "RFC-3339 date-time with second accuracy",
            "value": "2024-01-03T00:00:00Z"
          },
          "RFC-3339 with timezone": {
            "summary": "RFC-3339 date-time with time zone",
            "value": "2024-01-03T09:30:00-04:00"
          },
          "date": {
            "summary": "Date",
            "value": "2024-01-03"
          }
        },
        "in": "query",
        "name": "start",
        "required": false,
        "schema": {
          "format": "date-time",
          "type": "string"
        }
      },
      "stock_adjustment": {
        "description": "Specifies the adjustments for the bars.\n\n - `raw`: no adjustments\n - `split`: adjust price and volume for forward and reverse stock splits\n - `dividend`: adjust price for cash dividends\n - `spin-off`: adjust price for spin-offs\n - `all`: apply all above adjustments\n\nYou can combine multiple adjustments by separating them with a comma, e.g. `split,spin-off`.\n",
        "in": "query",
        "name": "adjustment",
        "schema": {
          "default": "raw",
          "type": "string"
        }
      },
      "stock_asof": {
        "description": "The as-of date of the queried stock symbol(s). Format: YYYY-MM-DD. Default: current day.\n\nThis date is used to identify the underlying entity of the provided symbol(s), so that name changes for this entity can be found. Data for past symbol(s) is returned if the query date range spans the name change.\n\nThe special value of \"-\" means symbol mapping is skipped. Data is returned based on the symbol alone without looking up previous names. The same happens if the queried symbol is not found on the given `asof` date.\n\nExample: FB was renamed to META in 2022-06-09. Querying META with an `asof` date after 2022-06-09 will also yield FB data. The data for the FB ticker will be labeled as META because they are considered the same underlying entity as of 2022-06-09. Querying FB with an `asof` date after 2022-06-09 will only return data with the FB ticker, not with META. But with an `asof` date before 2022-06-09, META will also be returned (as FB).\n",
        "in": "query",
        "name": "asof",
        "schema": {
          "type": "string"
        }
      },
      "stock_currency": {
        "description": "The currency of all prices in ISO 4217 format. Default: USD.\n",
        "in": "query",
        "name": "currency",
        "schema": {
          "type": "string"
        }
      },
      "stock_historical_feed": {
        "description": "The source feed of the data.\n - `sip`: all US exchanges\n - `iex`: Investors EXchange\n - `boats`: Blue Ocean ATS, overnight US trading data\n - `otc`: over-the-counter exchanges\n",
        "in": "query",
        "name": "feed",
        "schema": {
          "$ref": "#/components/schemas/stock_historical_feed"
        }
      },
      "stock_symbol": {
        "description": "The symbol to query.",
        "example": "AAPL",
        "in": "path",
        "name": "symbol",
        "required": true,
        "schema": {
          "type": "string"
        }
      },
      "timeframe": {
        "description": "The timeframe represented by each bar in aggregation.\nYou can use any of the following values:\n - `[1-59]Min` or `[1-59]T`, e.g. `5Min` or `5T` creates 5-minute aggregations\n - `[1-23]Hour` or `[1-23]H`, e.g. `12Hour` or `12H` creates 12-hour aggregations\n - `1Day` or `1D` creates 1-day aggregations\n - `1Week` or `1W` creates 1-week aggregations\n - `[1,2,3,4,6,12]Month` or `[1,2,3,4,6,12]M`, e.g. `3Month` or `3M` creates 3-month aggregations\n",
        "example": "1Min",
        "in": "query",
        "name": "timeframe",
        "required": true,
        "schema": {
          "type": "string"
        }
      }
    },
    "responses": {
      "400": {
        "description": "One of the request parameters is invalid. See the returned message for details.\n",
        "headers": {
          "X-RateLimit-Limit": {
            "$ref": "#/components/headers/ratelimit_limit"
          },
          "X-RateLimit-Remaining": {
            "$ref": "#/components/headers/ratelimit_remaining"
          },
          "X-RateLimit-Reset": {
            "$ref": "#/components/headers/ratelimit_reset"
          }
        }
      },
      "401": {
        "description": "Authentication headers are missing or invalid. Make sure you authenticate your request with a valid API key.\n"
      },
      "403": {
        "description": "The requested resource is forbidden.\n"
      },
      "429": {
        "description": "Too many requests. You hit the rate limit. Use the X-RateLimit-... response headers to make sure you're under the rate limit.\n",
        "headers": {
          "X-RateLimit-Limit": {
            "$ref": "#/components/headers/ratelimit_limit"
          },
          "X-RateLimit-Remaining": {
            "$ref": "#/components/headers/ratelimit_remaining"
          },
          "X-RateLimit-Reset": {
            "$ref": "#/components/headers/ratelimit_reset"
          }
        }
      },
      "500": {
        "description": "Internal server error. We recommend retrying these later. If the issue persists, please contact us on [Slack](https://alpaca.markets/slack) or on the [Community Forum](https://forum.alpaca.markets/).\n"
      }
    },
    "schemas": {
      "next_page_token": {
        "description": "Pagination token for the next page.",
        "type": [
          "string",
          "null"
        ]
      },
      "sort": {
        "default": "asc",
        "description": "Sort data in ascending or descending order.",
        "enum": [
          "asc",
          "desc"
        ],
        "type": "string"
      },
      "stock_bar": {
        "description": "OHLC aggregate of all the trades in a given interval.\n",
        "examples": [
          {
            "c": 178.08,
            "h": 178.34,
            "l": 177.76,
            "n": 1727,
            "o": 178.26,
            "t": "2022-01-03T09:00:00Z",
            "v": 60937,
            "vw": 177.954244
          }
        ],
        "properties": {
          "c": {
            "description": "Closing price.",
            "format": "double",
            "type": "number"
          },
          "h": {
            "description": "High price.",
            "format": "double",
            "type": "number"
          },
          "l": {
            "description": "Low price.",
            "format": "double",
            "type": "number"
          },
          "n": {
            "description": "Trade count in the bar.",
            "format": "int64",
            "type": "integer"
          },
          "o": {
            "description": "Opening price.",
            "format": "double",
            "type": "number"
          },
          "t": {
            "$ref": "#/components/schemas/timestamp"
          },
          "v": {
            "description": "Bar volume.",
            "format": "int64",
            "type": "integer"
          },
          "vw": {
            "description": "Volume weighted average price.",
            "format": "double",
            "type": "number"
          }
        },
        "required": [
          "t",
          "o",
          "h",
          "l",
          "c",
          "v",
          "n",
          "vw"
        ],
        "type": "object"
      },
      "stock_bars_resp_single": {
        "properties": {
          "bars": {
            "items": {
              "$ref": "#/components/schemas/stock_bar"
            },
            "type": "array"
          },
          "currency": {
            "type": "string"
          },
          "next_page_token": {
            "$ref": "#/components/schemas/next_page_token"
          },
          "symbol": {
            "type": "string"
          }
        },
        "required": [
          "bars",
          "next_page_token",
          "symbol"
        ],
        "type": "object"
      },
      "stock_historical_feed": {
        "default": "sip",
        "enum": [
          "iex",
          "otc",
          "sip",
          "boats"
        ],
        "type": "string"
      },
      "timestamp": {
        "description": "Timestamp in RFC-3339 format with nanosecond precision.",
        "format": "date-time",
        "type": "string"
      }
    },
    "securitySchemes": {
      "BasicAuth": {
        "scheme": "basic",
        "type": "http"
      },
      "apiKey": {
        "in": "header",
        "name": "APCA-API-KEY-ID",
        "type": "apiKey"
      },
      "apiSecret": {
        "in": "header",
        "name": "APCA-API-SECRET-KEY",
        "type": "apiKey"
      }
    }
  },
  "info": {
    "contact": {
      "email": "support@alpaca.markets",
      "name": "Alpaca Support",
      "url": "https://alpaca.markets/support"
    },
    "description": "Access real-time and historical market data for US equities, options, crypto, and foreign exchange data through the Alpaca REST and WebSocket APIs. There are APIs for Stock Pricing, Option Pricing, Crypto Pricing, Forex, Logos, Fixed income, Corporate Actions, Screener, and News.\n",
    "license": {
      "name": "Creative Commons Attribution Share Alike 4.0 International",
      "url": "https://spdx.org/licenses/CC-BY-SA-4.0.html"
    },
    "termsOfService": "https://s3.amazonaws.com/files.alpaca.markets/disclosures/library/TermsAndConditions.pdf",
    "title": "Market Data API",
    "version": "1.1"
  },
  "openapi": "3.1.2",
  "paths": {
    "/v2/stocks/{symbol}/bars": {
      "get": {
        "description": "The historical stock bars API provides aggregates for the stock symbol between the specified dates.",
        "operationId": "StockBarSingle",
        "parameters": [
          {
            "$ref": "#/components/parameters/stock_symbol"
          },
          {
            "$ref": "#/components/parameters/timeframe"
          },
          {
            "$ref": "#/components/parameters/start"
          },
          {
            "$ref": "#/components/parameters/end"
          },
          {
            "$ref": "#/components/parameters/limit"
          },
          {
            "$ref": "#/components/parameters/stock_adjustment"
          },
          {
            "$ref": "#/components/parameters/stock_asof"
          },
          {
            "$ref": "#/components/parameters/stock_historical_feed"
          },
          {
            "$ref": "#/components/parameters/stock_currency"
          },
          {
            "$ref": "#/components/parameters/page_token"
          },
          {
            "$ref": "#/components/parameters/sort"
          }
        ],
        "responses": {
          "200": {
            "content": {
              "application/json": {
                "examples": {
                  "bars": {
                    "value": {
                      "bars": [
                        {
                          "c": 178.21,
                          "h": 178.26,
                          "l": 178.21,
                          "n": 65,
                          "o": 178.26,
                          "t": "2022-01-03T09:00:00Z",
                          "v": 1118,
                          "vw": 178.235733
                        }
                      ],
                      "next_page_token": "QUFQTHxNfDIwMjItMDEtMDNUMDk6MDA6MDAuMDAwMDAwMDAwWg==",
                      "symbol": "AAPL"
                    }
                  }
                },
                "schema": {
                  "$ref": "#/components/schemas/stock_bars_resp_single"
                }
              }
            },
            "description": "OK",
            "headers": {
              "X-RateLimit-Limit": {
                "$ref": "#/components/headers/ratelimit_limit"
              },
              "X-RateLimit-Remaining": {
                "$ref": "#/components/headers/ratelimit_remaining"
              },
              "X-RateLimit-Reset": {
                "$ref": "#/components/headers/ratelimit_reset"
              }
            }
          },
          "400": {
            "$ref": "#/components/responses/400"
          },
          "401": {
            "$ref": "#/components/responses/401"
          },
          "403": {
            "$ref": "#/components/responses/403"
          },
          "429": {
            "$ref": "#/components/responses/429"
          },
          "500": {
            "$ref": "#/components/responses/500"
          }
        },
        "security": [
          {
            "apiKey": [],
            "apiSecret": []
          },
          {
            "BasicAuth": []
          }
        ],
        "summary": "Historical bars (single symbol)",
        "tags": [
          "Stock"
        ]
      }
    }
  },
  "servers": [
    {
      "description": "Production",
      "url": "https://data.alpaca.markets"
    },
    {
      "description": "Sandbox",
      "url": "https://data.sandbox.alpaca.markets"
    }
  ],
  "tags": [
    {
      "description": "Endpoints for stocks.",
      "name": "Stock"
    }
  ]
}
```