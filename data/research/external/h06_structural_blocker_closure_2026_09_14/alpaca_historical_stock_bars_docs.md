---
updatedAt: 2025-09-24T23:27:31.000Z
---

Fetch the complete documentation index at: https://docs.alpaca.markets/us/v1.4.2/llms.txt. Use this file to discover all available pages before exploring further. Append .md to any documentation page URL to get its markdown version.

# Historical bars

The historical stock bars API provides aggregates for a list of stock symbols between the specified dates.

The returned results are sorted by symbol first, then by bar timestamp. This means that you are likely to see only one symbol in your first response if there are enough bars for that symbol to hit the limit you requested.

In these situations, if you keep requesting again with the `next_page_token` from the previous response, you will eventually reach the other symbols if any bars were found for them.

# OpenAPI definition

```json
{
  "openapi": "3.0.0",
  "info": {
    "title": "Market Data API",
    "description": "Access real-time and historical market data for US equities, options, crypto, and foreign exchange data through the Alpaca REST and WebSocket APIs. There are APIs for Stock Pricing, Option Pricing, Crypto Pricing, Forex, Logos, Fixed income, Corporate Actions, Screener, and News.\n",
    "version": "1.1",
    "contact": {
      "name": "Alpaca Support",
      "email": "support@alpaca.markets",
      "url": "https://alpaca.markets/support"
    },
    "termsOfService": "https://s3.amazonaws.com/files.alpaca.markets/disclosures/library/TermsAndConditions.pdf",
    "license": {
      "name": "Creative Commons Attribution Share Alike 4.0 International",
      "url": "https://spdx.org/licenses/CC-BY-SA-4.0.html"
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
  "security": [
    {
      "apiKey": [],
      "apiSecret": []
    }
  ],
  "tags": [
    {
      "name": "Stock",
      "description": "Endpoints for stocks."
    }
  ],
  "paths": {
    "/v2/stocks/bars": {
      "get": {
        "summary": "Historical bars",
        "tags": [
          "Stock"
        ],
        "parameters": [
          {
            "$ref": "#/components/parameters/stock_symbols"
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
            },
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/stock_bars_resp"
                },
                "examples": {
                  "bars": {
                    "value": {
                      "bars": {
                        "AAPL": [
                          {
                            "t": "2022-01-03T09:00:00Z",
                            "o": 178.26,
                            "h": 178.26,
                            "l": 178.21,
                            "c": 178.21,
                            "v": 1118,
                            "n": 65,
                            "vw": 178.235733
                          }
                        ]
                      },
                      "next_page_token": "QUFQTHxNfDIwMjItMDEtMDNUMDk6MDA6MDAuMDAwMDAwMDAwWg=="
                    }
                  }
                }
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
        "operationId": "StockBars",
        "description": "The historical stock bars API provides aggregates for a list of stock symbols between the specified dates.\n\nThe returned results are sorted by symbol first, then by bar timestamp. This means that you are likely to see only one symbol in your first response if there are enough bars for that symbol to hit the limit you requested.\n\nIn these situations, if you keep requesting again with the `next_page_token` from the previous response, you will eventually reach the other symbols if any bars were found for them."
      }
    }
  },
  "components": {
    "securitySchemes": {
      "apiKey": {
        "type": "apiKey",
        "in": "header",
        "name": "APCA-API-KEY-ID"
      },
      "apiSecret": {
        "type": "apiKey",
        "in": "header",
        "name": "APCA-API-SECRET-KEY"
      }
    },
    "parameters": {
      "page_token": {
        "name": "page_token",
        "in": "query",
        "schema": {
          "type": "string"
        },
        "description": "The pagination token from which to continue. The value to pass here is returned in specific requests when more data is available, usually because of a response result limit.\n"
      },
      "sort": {
        "name": "sort",
        "in": "query",
        "description": "Sort data in ascending or descending order.",
        "schema": {
          "$ref": "#/components/schemas/sort"
        }
      },
      "start": {
        "name": "start",
        "in": "query",
        "required": false,
        "schema": {
          "type": "string",
          "format": "date-time"
        },
        "examples": {
          "RFC-3339 second": {
            "value": "2024-01-03T00:00:00Z",
            "summary": "RFC-3339 date-time with second accuracy"
          },
          "RFC-3339 nanosecond": {
            "value": "2024-01-03T01:02:03.123456789Z",
            "summary": "RFC-3339 date-time with nanosecond accuracy"
          },
          "RFC-3339 with timezone": {
            "value": "2024-01-03T09:30:00-04:00",
            "summary": "RFC-3339 date-time with time zone"
          },
          "date": {
            "value": "2024-01-03",
            "summary": "Date"
          }
        },
        "description": "The inclusive start of the interval. Format: RFC-3339 or YYYY-MM-DD.\nDefault: the beginning of the current day, but at least 15 minutes ago if the user doesn't have real-time access for the feed.\n"
      },
      "end": {
        "name": "end",
        "in": "query",
        "required": false,
        "schema": {
          "type": "string",
          "format": "date-time"
        },
        "examples": {
          "RFC-3339 second": {
            "value": "2024-01-04T00:00:00Z",
            "summary": "RFC-3339 date-time with second accuracy"
          },
          "RFC-3339 nanosecond": {
            "value": "2024-01-04T01:02:03.123456789Z",
            "summary": "RFC-3339 date-time with nanosecond accuracy"
          },
          "RFC-3339 with timezone": {
            "value": "2024-01-04T09:30:00-04:00",
            "summary": "RFC-3339 date-time with time zone"
          },
          "date": {
            "value": "2024-01-04",
            "summary": "Date"
          }
        },
        "description": "The inclusive end of the interval. Format: RFC-3339 or YYYY-MM-DD.\nDefault: the current time if the user has a real-time access for the feed, otherwise 15 minutes before the current time.\n"
      },
      "limit": {
        "name": "limit",
        "in": "query",
        "required": false,
        "schema": {
          "type": "integer",
          "minimum": 1,
          "maximum": 10000,
          "default": 1000
        },
        "description": "The maximum number of data points to return in the response page.\nThe API may return less, even if there are more available data points in the requested interval.\nAlways check the `next_page_token` for more pages.\nThe limit applies to the total number of data points, not per symbol!\n"
      },
      "timeframe": {
        "name": "timeframe",
        "in": "query",
        "schema": {
          "type": "string"
        },
        "required": true,
        "x-go-name": "TimeFrame",
        "example": "1Min",
        "description": "The timeframe represented by each bar in aggregation.\nYou can use any of the following values:\n - `[1-59]Min` or `[1-59]T`, e.g. `5Min` or `5T` creates 5-minute aggregations\n - `[1-23]Hour` or `[1-23]H`, e.g. `12Hour` or `12H` creates 12-hour aggregations\n - `1Day` or `1D` creates 1-day aggregations\n - `1Week` or `1W` creates 1-week aggregations\n - `[1,2,3,4,6,12]Month` or `[1,2,3,4,6,12]M`, e.g. `3Month` or `3M` creates 3-month aggregations\n"
      },
      "stock_symbols": {
        "name": "symbols",
        "description": "A comma-separated list of stock symbols.",
        "in": "query",
        "required": true,
        "schema": {
          "type": "string"
        },
        "example": "AAPL,TSLA"
      },
      "stock_asof": {
        "name": "asof",
        "in": "query",
        "description": "The as-of date of the queried stock symbol(s). Format: YYYY-MM-DD. Default: current day.\n\nThis date is used to identify the underlying entity of the provided symbol(s), so that name changes for this entity can be found. Data for past symbol(s) is returned if the query date range spans the name change.\n\nThe special value of \"-\" means symbol mapping is skipped. Data is returned based on the symbol alone without looking up previous names. The same happens if the queried symbol is not found on the given `asof` date.\n\nExample: FB was renamed to META in 2022-06-09. Querying META with an `asof` date after 2022-06-09 will also yield FB data. The data for the FB ticker will be labeled as META because they are considered the same underlying entity as of 2022-06-09. Querying FB with an `asof` date after 2022-06-09 will only return data with the FB ticker, not with META. But with an `asof` date before 2022-06-09, META will also be returned (as FB).\n",
        "schema": {
          "type": "string"
        }
      },
      "stock_currency": {
        "name": "currency",
        "in": "query",
        "description": "The currency of all prices in ISO 4217 format. Default: USD.\n",
        "schema": {
          "type": "string"
        }
      },
      "stock_adjustment": {
        "name": "adjustment",
        "in": "query",
        "description": "Specifies the adjustments for the bars.\n\n - `raw`: no adjustments\n - `split`: adjust price and volume for forward and reverse stock splits\n - `dividend`: adjust price for cash dividends\n - `spin-off`: adjust price for spin-offs\n - `all`: apply all above adjustments\n\nYou can combine multiple adjustments by separating them with a comma, e.g. `split,spin-off`.\n",
        "schema": {
          "type": "string",
          "default": "raw"
        }
      },
      "stock_historical_feed": {
        "name": "feed",
        "in": "query",
        "description": "The source feed of the data.\n - `sip`: all US exchanges\n - `iex`: Investors EXchange\n - `boats`: Blue Ocean ATS, overnight US trading data\n - `otc`: over-the-counter exchanges\n",
        "schema": {
          "$ref": "#/components/schemas/stock_historical_feed"
        }
      }
    },
    "schemas": {
      "sort": {
        "type": "string",
        "description": "Sort data in ascending or descending order.",
        "enum": [
          "asc",
          "desc"
        ],
        "default": "asc",
        "x-go-name": "TypeSort"
      },
      "next_page_token": {
        "type": "string",
        "description": "Pagination token for the next page.",
        "nullable": true
      },
      "timestamp": {
        "type": "string",
        "description": "Timestamp in RFC-3339 format with nanosecond precision.",
        "format": "date-time",
        "x-go-name": "Timestamp"
      },
      "stock_historical_feed": {
        "type": "string",
        "enum": [
          "iex",
          "otc",
          "sip",
          "boats"
        ],
        "default": "sip"
      },
      "stock_bar": {
        "type": "object",
        "description": "OHLC aggregate of all the trades in a given interval.\n",
        "example": {
          "t": "2022-01-03T09:00:00Z",
          "o": 178.26,
          "h": 178.34,
          "l": 177.76,
          "c": 178.08,
          "v": 60937,
          "n": 1727,
          "vw": 177.954244
        },
        "properties": {
          "t": {
            "$ref": "#/components/schemas/timestamp"
          },
          "o": {
            "type": "number",
            "format": "double",
            "description": "Opening price.",
            "x-go-name": "Open"
          },
          "h": {
            "type": "number",
            "format": "double",
            "description": "High price.",
            "x-go-name": "High"
          },
          "l": {
            "type": "number",
            "format": "double",
            "description": "Low price.",
            "x-go-name": "Low"
          },
          "c": {
            "type": "number",
            "format": "double",
            "description": "Closing price.",
            "x-go-name": "Close"
          },
          "v": {
            "type": "integer",
            "format": "int64",
            "description": "Bar volume.",
            "x-go-name": "Volume"
          },
          "n": {
            "type": "integer",
            "format": "int64",
            "description": "Trade count in the bar.",
            "x-go-name": "TradeCount"
          },
          "vw": {
            "type": "number",
            "format": "double",
            "description": "Volume weighted average price.",
            "x-go-name": "VWAP"
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
        ]
      },
      "stock_bars_resp": {
        "type": "object",
        "properties": {
          "bars": {
            "type": "object",
            "additionalProperties": {
              "type": "array",
              "items": {
                "$ref": "#/components/schemas/stock_bar"
              }
            }
          },
          "next_page_token": {
            "$ref": "#/components/schemas/next_page_token"
          },
          "currency": {
            "type": "string"
          }
        },
        "required": [
          "bars",
          "next_page_token"
        ]
      }
    },
    "headers": {
      "ratelimit_limit": {
        "schema": {
          "type": "integer"
        },
        "example": 100,
        "description": "Request limit per minute."
      },
      "ratelimit_remaining": {
        "schema": {
          "type": "integer"
        },
        "example": 90,
        "description": "Request limit per minute remaining."
      },
      "ratelimit_reset": {
        "schema": {
          "type": "integer"
        },
        "example": 1674044551,
        "description": "The UNIX epoch when the remaining quota changes."
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
    }
  }
}
```